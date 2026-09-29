"""Infraestructura EC2 reproducible, sin secretos en plantilla o user-data.

Genera CloudFormation; su despliegue es un paso explícito por AWS CLI.
"""

import json
from pathlib import Path


def template():
    bootstrap = Path(__file__).with_name("bootstrap.sh").read_text()
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "ASISTIA: EC2, IPv4 fija, SSM y respaldo privado; PostgreSQL sin acceso publico",
        "Parameters": {
            "VpcId": {"Type": "AWS::EC2::VPC::Id"},
            "SubnetId": {"Type": "AWS::EC2::Subnet::Id"},
            "ImageId": {"Type": "AWS::EC2::Image::Id"},
        },
        "Resources": {
            "Storage": {
                "Type": "AWS::S3::Bucket",
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": {
                    "BucketEncryption": {
                        "ServerSideEncryptionConfiguration": [
                            {
                                "ServerSideEncryptionByDefault": {
                                    "SSEAlgorithm": "AES256"
                                }
                            }
                        ]
                    },
                    "VersioningConfiguration": {"Status": "Enabled"},
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "IgnorePublicAcls": True,
                        "BlockPublicPolicy": True,
                        "RestrictPublicBuckets": True,
                    },
                    "OwnershipControls": {
                        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
                    },
                    "LifecycleConfiguration": {
                        "Rules": [
                            {
                                "Id": "db-retention",
                                "Status": "Enabled",
                                "Prefix": "backups/db/",
                                "ExpirationInDays": 30,
                                "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                            },
                            {
                                "Id": "staging-retention",
                                "Status": "Enabled",
                                "Prefix": "staging/",
                                "ExpirationInDays": 7,
                                "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
                            },
                            {
                                "Id": "incomplete-uploads",
                                "Status": "Enabled",
                                "AbortIncompleteMultipartUpload": {
                                    "DaysAfterInitiation": 2
                                },
                            },
                        ]
                    },
                    "Tags": [{"Key": "Project", "Value": "ASISTIA"}],
                },
            },
            "StoragePolicy": {
                "Type": "AWS::S3::BucketPolicy",
                "Properties": {
                    "Bucket": {"Ref": "Storage"},
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Deny",
                                "Principal": "*",
                                "Action": "s3:*",
                                "Resource": [
                                    {"Fn::GetAtt": ["Storage", "Arn"]},
                                    {"Fn::Sub": "${Storage.Arn}/*"},
                                ],
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        ],
                    },
                },
            },
            "InstanceRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"Service": "ec2.amazonaws.com"},
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    },
                    "ManagedPolicyArns": [
                        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
                    ],
                    "Policies": [
                        {
                            "PolicyName": "AsistiaStorage",
                            "PolicyDocument": {
                                "Version": "2012-10-17",
                                "Statement": [
                                    {
                                        "Effect": "Allow",
                                        "Action": [
                                            "s3:ListBucket",
                                            "s3:ListBucketVersions",
                                            "s3:GetBucketLocation",
                                        ],
                                        "Resource": {"Fn::GetAtt": ["Storage", "Arn"]},
                                    },
                                    {
                                        "Effect": "Allow",
                                        "Action": [
                                            "s3:GetObject",
                                            "s3:GetObjectVersion",
                                        ],
                                        "Resource": [
                                            {"Fn::Sub": "${Storage.Arn}/staging/*"},
                                            {"Fn::Sub": "${Storage.Arn}/releases/*"},
                                            {"Fn::Sub": "${Storage.Arn}/backups/*"},
                                        ],
                                    },
                                    {
                                        "Effect": "Allow",
                                        "Action": [
                                            "s3:PutObject",
                                            "s3:AbortMultipartUpload",
                                        ],
                                        "Resource": {
                                            "Fn::Sub": "${Storage.Arn}/backups/*"
                                        },
                                    },
                                ],
                            },
                        }
                    ],
                },
            },
            "InstanceProfile": {
                "Type": "AWS::IAM::InstanceProfile",
                "Properties": {"Roles": [{"Ref": "InstanceRole"}]},
            },
            "WebSecurityGroup": {
                "Type": "AWS::EC2::SecurityGroup",
                "Properties": {
                    "GroupDescription": "HTTPS publico; HTTP solo ACME/redireccion; administracion por SSM",
                    "VpcId": {"Ref": "VpcId"},
                    "SecurityGroupIngress": [
                        {
                            "IpProtocol": "tcp",
                            "FromPort": p,
                            "ToPort": p,
                            "CidrIp": "0.0.0.0/0",
                        }
                        for p in (80, 443)
                    ],
                    "Tags": [{"Key": "Name", "Value": "asistia-production"}],
                },
            },
            "Server": {
                "Type": "AWS::EC2::Instance",
                "Properties": {
                    "ImageId": {"Ref": "ImageId"},
                    "InstanceType": "t3a.medium",
                    "SubnetId": {"Ref": "SubnetId"},
                    "SecurityGroupIds": [{"Ref": "WebSecurityGroup"}],
                    "IamInstanceProfile": {"Ref": "InstanceProfile"},
                    "MetadataOptions": {
                        "HttpTokens": "required",
                        "HttpPutResponseHopLimit": 1,
                    },
                    "CreditSpecification": {"CPUCredits": "standard"},
                    "BlockDeviceMappings": [
                        {
                            "DeviceName": "/dev/sda1",
                            "Ebs": {
                                "VolumeSize": 40,
                                "VolumeType": "gp3",
                                "Encrypted": True,
                                "DeleteOnTermination": False,
                            },
                        }
                    ],
                    "UserData": {"Fn::Base64": bootstrap},
                    "Tags": [
                        {"Key": "Name", "Value": "asistia-production"},
                        {"Key": "Project", "Value": "ASISTIA"},
                    ],
                },
            },
            "Address": {
                "Type": "AWS::EC2::EIP",
                "Properties": {
                    "Domain": "vpc",
                    "Tags": [{"Key": "Project", "Value": "ASISTIA"}],
                },
            },
            "AddressAssociation": {
                "Type": "AWS::EC2::EIPAssociation",
                "Properties": {
                    "AllocationId": {"Fn::GetAtt": ["Address", "AllocationId"]},
                    "InstanceId": {"Ref": "Server"},
                },
            },
            "MonthlyBudget": {
                "Type": "AWS::Budgets::Budget",
                "Properties": {
                    "Budget": {
                        "BudgetName": "ASISTIA-monthly-40-USD",
                        "BudgetType": "COST",
                        "CostTypes": {"IncludeCredit": False, "IncludeRefund": False},
                        "TimeUnit": "MONTHLY",
                        "BudgetLimit": {"Amount": 40, "Unit": "USD"},
                    },
                },
            },
        },
        "Outputs": {
            "InstanceId": {"Value": {"Ref": "Server"}},
            "PublicIp": {"Value": {"Ref": "Address"}},
            "Bucket": {"Value": {"Ref": "Storage"}},
            "SecurityGroupId": {"Value": {"Ref": "WebSecurityGroup"}},
        },
    }


if __name__ == "__main__":
    print(json.dumps(template(), indent=2))
