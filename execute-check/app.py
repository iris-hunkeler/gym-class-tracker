import json
import boto3
import os
from boto3.dynamodb.conditions import Key, Attr
import requests

STATUS_UNKNOWN = "Unknown"
STATUS_AVAILABLE = "Available"
STATUS_UNAVAILABLE = "Unavailable"

# API for REST call - this is not an official API and does not guarantee backwards compatability!
ACTIVE_FITNESS_API_URL = 'https://blfa-api.migros.ch/kp/api/Courselist/all?'

def lambda_handler(event, context):
    table = _get_dynamo_db_table()

    tracker_queries = _read_tracker_queries_from_dynamo_db(table)

    successful_count = 0
    count = 0
    for tracker_query in tracker_queries:
        partition_key = tracker_query['partition-key']
        print(f'checking tracker-query {partition_key}')
        is_successful = _check_tracker_query_against_gym_api(table, tracker_query)

        count = count + 1
        if is_successful:
            successful_count = successful_count + 1            

    message = f'Checked {count} tracker queries, {successful_count} courses are currently bookable'
    print(message)
    return {
        'statusCode': 200,
        'body': json.dumps({'message': message})
    }

def _check_tracker_query_against_gym_api(table, tracker_query):
    print('tracker_query:', tracker_query)

    # API call
    json_data = _build_body_for_api_request(tracker_query['course-title'], 
                            tracker_query['center-id'], 
                            tracker_query['daytime-id'], 
                            tracker_query['weekday-id'])
    response = _make_request_to_api(json_data)

    # Validation
    is_valid = _api_response_error_handling(response)
    if not is_valid:
        return False
    
    response_json = response.json()
    print('response from API:', response_json)

    # Check status  
    _initialize_or_update_active_status(table, tracker_query, response_json)
    status_new = _update_availability_status(table, tracker_query, response_json)
    
    if status_new == STATUS_AVAILABLE:
        return True
    else:
        return False

def _api_response_error_handling(response):
    if response.status_code != 200:   
        message = f'There is an error. The API returned {response.status_code}'     
        print(f'{message}, let\'s send a notification.')
        _send_notification(message)
        return False
    
    if not _check_at_least_one_course_found(response):
        message = 'No course found for the search criteria.'     
        print(f'{message}, let\'s send a notification.')
        _send_notification(message)
        return False
    
    return True

def _initialize_or_update_active_status(table, tracker_query, response_json):
    course = _extract_course_from_api_response(response_json)
    course_id = _extract_course_id_from_course(course)
    course_date = _extract_date_from_course(course)
    course_instructor = _extract_instructor_from_course(course)
    course_name = _extract_course_name_from_course(course)
    if 'course-id' not in tracker_query:
        _initialize_tracker_query_course_information_in_dynamo_db(table, tracker_query, course_id, course_instructor, course_date)
        _send_notification(f'TRACKING STARTED: You are now tracking {_build_course_description(course_name, course_instructor, course_date)}')
    elif tracker_query['course-id'] != course_id:
        _update_tracker_active_status_in_dynamo_db(table, tracker_query)
        _send_notification(f'TRACKING ENDED: You are no longer tracking {_build_course_description(course_name, course_instructor, course_date)}')

def _update_availability_status(table, tracker_query, response_json):
    status_old = tracker_query['availability-status']
    status_new = _extract_status_from_api_response(response_json)
    
    if status_old != status_new:
        print('Status has changed, let\'s send a notification.')
        message = _build_message_for_availability_status_notification(response_json)
        _send_notification(message)
        _update_tracker_status_query_in_dynamo_db(table, tracker_query, status_new)
    
    return status_new

def _build_message_for_availability_status_notification(response):
    status_new = _extract_status_from_api_response(response)
    course_description = _build_course_description_from_response(response)

    if status_new == STATUS_AVAILABLE:
        return f'YES: Course {course_description} is now bookable'
    elif status_new == STATUS_UNAVAILABLE:
        return f'NO: Course {course_description} is not bookable anymore!'
    else:
        return f'WHAT!? The status of course {course_description} is unknown.'


# API HELPERS
def _build_body_for_api_request(course_title, center_id, daytime_id, weekday_id):
    data = {
        "language": "de",
        "skip": 0,
        "take": 1,
        "selectMethod": 1,
        "memberIdTac": 0,
        "centerIds": [center_id],
        "daytimeIds": [daytime_id],
        "weekdayIds": [weekday_id],
        "coursetitles": [
            {
                "centerId": center_id,
                "coursetitle": course_title
            }
        ]
    }
    
    return json.dumps(data)

def _make_request_to_api(json_data):
    headers = {
        'Content-Type': 'application/json'
    }

    response = requests.post(ACTIVE_FITNESS_API_URL, 
                             headers=headers, 
                             data=json_data)
    print('HTTP status code:', response.status_code)

    return response

def _check_at_least_one_course_found(response):
    response_json = response.json()
    courses = response_json['courses']
    if len(courses) < 1:
        return False
    return True

def _extract_course_from_api_response(response):
    # assume the search criteria have a unique result
    return response['courses'][0]

def _extract_status_from_api_response(response):
    status_new = STATUS_UNKNOWN
    course = _extract_course_from_api_response(response)
    course_description = _build_course_description_from_response(response)
    if course['bookable']:
        status_new = STATUS_AVAILABLE
        print(f'Course {course_description} is currently bookable!')
    else:
        status_new = STATUS_UNAVAILABLE
        print(f'Course {course_description} is (still) not bookable :(')
    
    return status_new

def _extract_course_name_from_course(course):
    return course['title']

def _extract_instructor_from_course(course):
    return course['instructor']

def _extract_date_from_course(course):
    return course['start']

def _extract_course_id_from_course(course):
    return course['courseIdTac']

def _build_course_description_from_response(response):
    course = _extract_course_from_api_response(response)
    return _build_course_description(_extract_course_name_from_course(course), _extract_instructor_from_course(course), _extract_date_from_course(course))

def _build_course_description(course_name, course_instructor, course_date):
    return f'{course_name} by {course_instructor} on {course_date}'



# SNS HELPERS
def _get_sns_topic_arn():
    topic_arn = os.environ['SNS_TOPIC_ARN']
    return topic_arn

def _send_notification(message):
    sns = boto3.client('sns')
    sns.publish(
            TopicArn=_get_sns_topic_arn(),
            Message=message
        )


# DYNAMO DB HELPERS
def _get_dynamo_db_table():
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('gym-class-tracker-table')   
    return table 

def _read_tracker_queries_from_dynamo_db(table):
    response = table.query(
        IndexName='EntityNameIndex', 
        KeyConditionExpression=Key('entity-name').eq('check-query'),
        FilterExpression=Attr('active-status').ne(False)
    )
    return response['Items']

def _update_tracker_status_query_in_dynamo_db(table, tracker_query, status_new):
    table.update_item(
        Key={'partition-key': tracker_query['partition-key'], 'sort-key': tracker_query['sort-key']},
        UpdateExpression="set #s=:status_new",
        ExpressionAttributeNames={'#s': 'availability-status'},
        ExpressionAttributeValues={':status_new': status_new}
    )

def _initialize_tracker_query_course_information_in_dynamo_db(table, tracker_query, course_id, instructor, date):
    table.update_item(
        Key={'partition-key': tracker_query['partition-key'], 'sort-key': tracker_query['sort-key']},
        UpdateExpression="set #s=:course_id, #i=:instructor_name, #d=:date",
        ExpressionAttributeNames={'#s': 'course-id', '#i': 'instructor', '#d': 'course-date'},
        ExpressionAttributeValues={':course_id': course_id, ':instructor_name': instructor, ':date': date}
    )

def _update_tracker_active_status_in_dynamo_db(table, tracker_query):
    table.update_item(
        Key={'partition-key': tracker_query['partition-key'], 'sort-key': tracker_query['sort-key']},
        UpdateExpression="set #s=:active_status",
        ExpressionAttributeNames={'#s': 'active-status'},
        ExpressionAttributeValues={':active_status': False}
    )