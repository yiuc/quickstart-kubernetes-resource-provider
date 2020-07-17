import boto3
import os
import traceback
from string import ascii_lowercase
from random import choice
import json
import logging
import time

from cloudformation_cli_python_lib import ProgressEvent
from .models import ResourceModel


LOG = logging.getLogger(__name__)


def proxy_needed(cluster_name: str, boto3_session: boto3.Session) -> (boto3.client, str):
    eks = boto3_session.client('eks')
    eks_vpc_config = eks.describe_cluster(name=cluster_name)['cluster']['resourcesVpcConfig']
    if eks_vpc_config['endpointPublicAccess'] and '0.0.0.0/0' in eks_vpc_config['publicAccessCidrs']:
        return False
    if this_invoke_is_inside_vpc(set(eks_vpc_config['subnetIds']), set(eks_vpc_config['securityGroupIds'])):
        return False
    return True


def this_invoke_is_inside_vpc(subnet_ids: set, sg_ids: set) -> bool:
    lmbd = boto3.client('lambda')
    try:
        lambda_config = lmbd.get_function_configuration(FunctionName=os.environ['AWS_LAMBDA_FUNCTION_NAME'])
        l_vpc_id = lambda_config['VpcConfig'].get('VpcId', '')
        l_subnet_ids = set(lambda_config['VpcConfig'].get('subnetIds', ''))
        l_sg_ids = set(lambda_config['VpcConfig'].get('securityGroupIds', ''))
        if l_vpc_id and l_subnet_ids.issubset(subnet_ids) and l_sg_ids.issubset(sg_ids):
            return True
    except Exception as e:
        print(f'failed to get function config for {os.environ["AWS_LAMBDA_FUNCTION_NAME"]}')
        traceback.print_exc()
    return False


def proxy_call(cluster_name, manifest, command, sess):
    event = {
        "cluster_name": cluster_name,
        "manifest": manifest,
        "command": command
    }
    resp = invoke_function(f'awsqs-kubernetes-resource-apply-proxy-{cluster_name}', event, sess)
    if 'errorMessage' in resp:
        LOG.error(f'{resp["errorType"]}: {resp["errorMessage"]}')
        LOG.error(f'{resp["stackTrace"]}')
        raise Exception(f'{resp["errorType"]}: {resp["errorMessage"]}')
    return resp


def random_string(length=8):
    return ''.join(choice(ascii_lowercase) for _ in range(length))


def put_function(sess, cluster_name):
    eks = sess.client('eks')
    eks_vpc_config = eks.describe_cluster(name=cluster_name)['cluster']['resourcesVpcConfig']
    ec2 = sess.client('ec2')
    internal_subnets = [
        s['SubnetId'] for s in
        ec2.describe_subnets(SubnetIds=eks_vpc_config['subnetIds'], Filters=[
            {'Name': "tag-key", "Values": ['kubernetes.io/role/internal-elb']}
        ])['Subnets']
    ]
    sts = sess.client('sts')
    role_arn = '/'.join(sts.get_caller_identity()['Arn'].replace(':sts:', ':iam:').replace(':assumed-role/', ':role/')
                        .split('/')[:-1])
    lmbd = sess.client('lambda')
    try:
        with open('./awsqs_kubernetes_resource/vpc.zip', 'rb') as zip_file:
            lmbd.create_function(
                FunctionName=f'awsqs-kubernetes-resource-apply-proxy-{cluster_name}',
                Runtime='python3.7',
                Role=role_arn,
                Handler="awsqs_kubernetes_resource.handlers.proxy_wrap",
                Code={'ZipFile': zip_file.read()},
                Timeout=900,
                MemorySize=512,
                VpcConfig={
                    'SubnetIds': internal_subnets,
                    'SecurityGroupIds': eks_vpc_config['securityGroupIds']
                }
            )
    except lmbd.exceptions.ResourceConflictException as e:
        if "Function already exist" not in str(e):
            raise
        LOG.warning("function already exists...")
        with open('./awsqs_kubernetes_resource/vpc.zip', 'rb') as zip_file:
            lmbd.update_function_code(
                FunctionName=f'awsqs-kubernetes-resource-apply-proxy-{cluster_name}',
                ZipFile=zip_file.read()
            )
        lmbd.update_function_configuration(
            FunctionName=f'awsqs-kubernetes-resource-apply-proxy-{cluster_name}',
            Runtime='python3.7',
            Role=role_arn,
            Handler="awsqs_kubernetes_resource.handlers.proxy_wrap",
            Timeout=900,
            MemorySize=512,
            VpcConfig={
                'SubnetIds': internal_subnets,
                'SecurityGroupIds': eks_vpc_config['securityGroupIds']
            }
        )


def invoke_function(func_arn, event, sess):
    lmbd = sess.client('lambda')
    while True:
        try:
            response = lmbd.invoke(
                FunctionName=func_arn,
                InvocationType='RequestResponse',
                Payload=json.dumps(event).encode('utf-8')
            )
            return json.loads(response['Payload'].read().decode('utf-8'))
        except lmbd.exceptions.ResourceConflictException as e:
            if "The operation cannot be performed at this time." not in str(e):
                raise
            LOG.error(str(e))
            time.sleep(10)
