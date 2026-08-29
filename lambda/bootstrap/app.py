"""
CloudFormation custom resource: creates the two demo Cognito users with
permanent passwords (CFN's native AWS::Cognito::UserPoolUser can't set a
usable permanent password) and seeds their DynamoDB student records. Both
users share the same DynamoDB key as their Cognito username, so the
orchestrator can go straight from JWT claim to table key with no extra
mapping table.

Deletion is a no-op: this stack is a throwaway demo environment, so on
DELETE we just report success without trying to laboriously unwind Cognito
users/DynamoDB items before the parent resources are torn down anyway.
"""
import json
import urllib.request

import boto3

cognito = boto3.client("cognito-idp")
dynamodb = boto3.resource("dynamodb")


def handler(event, context):
    try:
        if event["RequestType"] in ("Create", "Update"):
            _seed(event["ResourceProperties"])
        _send(event, context, "SUCCESS")
    except Exception as e:
        _send(event, context, "FAILED", reason=str(e))


def _seed(props: dict) -> None:
    table = dynamodb.Table(props["TableName"])
    user_pool_id = props["UserPoolId"]

    for username_key, password_key, record_key in (
        ("UserAUsername", "UserAPassword", "UserARecord"),
        ("UserBUsername", "UserBPassword", "UserBRecord"),
    ):
        username = props[username_key]
        _ensure_cognito_user(user_pool_id, username, props[password_key])
        record = dict(props[record_key])
        record["student_id"] = username
        table.put_item(Item=record)


def _ensure_cognito_user(user_pool_id: str, username: str, password: str) -> None:
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=username,
            MessageAction="SUPPRESS",
        )
    except cognito.exceptions.UsernameExistsException:
        pass

    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )


def _send(event, context, status, reason=None, data=None):
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream {context.log_stream_name}",
            "PhysicalResourceId": event.get("PhysicalResourceId") or context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": data or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT", headers={"Content-Type": ""}
    )
    urllib.request.urlopen(req)
