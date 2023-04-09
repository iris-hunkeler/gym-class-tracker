import json
import boto3
import requests

# manually deploy lambda
# manually create scheduler to call lambda regularly (also needs to be manually stopped after class!)
def lambda_handler(event, context):

    # SNS topic
    sns_topic_arn = 'arn:aws:sns:us-east-1:121870419051:gym'

    # URL of gym API
    url = 'https://blfa-api.migros.ch/kp/api/Courselist/all?'
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    # Data structure expected by gym API, search values are hard-coded
    data = {
        "language": "de",
        "skip": 0,
        "take": 1,
        "selectMethod": 1,
        "memberIdTac": 0,
        "centerIds": [
            23
        ],
        "daytimeIds": [],
        "weekdayIds": [
            5
        ],
        "coursetitles": [
            {
                "centerId": 23,
                "coursetitle": "BODYPUMP® 55'"
            }
        ]
    }
    json_data = json.dumps(data)

    # Make request to gym API and print status code
    response = requests.post(url, headers=headers, data=json_data)
    print('HTTP status code:', response.status_code)
    
    sns = boto3.client('sns')
    message = 'Unknown'
    
    if response.status_code != 200:
        # Uh-oh, something went wrong. Can't handle errors, so let's send a notification.
        message = 'There is an error'
        
        print(f'{message}, let\'s send a notification.')
        sns.publish(
            TopicArn=sns_topic_arn,
            Message=message
        )
    else:
        # Huh, call was successful, let's check availability
        print('response:', response.json())
        
        # Let's assume the search criteria are unique, so we just check the first course returned
        course = response.json()['courses'][0]
        if course['bookable']:
            # Course is bookable!! Send a notification 
            # since there is no state, EVERY TIME this lambda is called, we send a notification ...
            message='Course is now bookable'
            
            print(f'{message}, let\'s send a notification.')
            sns.publish(
                TopicArn=sns_topic_arn,
                Message=message
            )
        else:
            # Course is not bookable, not worth informing the user
            message='Course is still not bookable :('
            print(message)

    # Return a JSON response with the message of the course state
    return {
        'statusCode': 200,
        'body': json.dumps({'message': message})
    }