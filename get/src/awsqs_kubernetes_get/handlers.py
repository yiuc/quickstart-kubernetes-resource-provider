import logging
from typing import Any, MutableMapping, Optional
import json
import subprocess
import shlex
import time
from hashlib import md5
import boto3
import hashlib
import os

from cloudformation_cli_python_lib import (
    Action,
    HandlerErrorCode,
    OperationStatus,
    ProgressEvent,
    Resource,
    SessionProxy,
    exceptions,
)

from .models import ResourceHandlerRequest, ResourceModel
from .vpc import proxy_needed, proxy_call, put_function

# Use this logger to forward log messages to CloudWatch Logs.
LOG = logging.getLogger(__name__)
TYPE_NAME = "AWSQS::Kubernetes::Get"
LOG.setLevel(logging.INFO)


resource = Resource(TYPE_NAME, ResourceModel)
test_entrypoint = resource.test_entrypoint


def run_command(command):
    try:
        LOG.info("executing command: %s" % command)
        output = subprocess.check_output(shlex.split(command), stderr=subprocess.STDOUT).decode("utf-8")
        LOG.info(output)
    except subprocess.CalledProcessError as exc:
        LOG.error("Command failed with exit code %s, stderr: %s" % (exc.returncode, exc.output.decode("utf-8")))
        raise Exception(exc.output.decode("utf-8"))
    return output


def create_kubeconfig(cluster_name):
    os.environ['PATH'] = f"/var/task/bin:{os.environ['PATH']}"
    os.environ['PYTHONPATH'] = f"/var/task:{os.environ.get('PYTHONPATH', '')}"
    os.environ['KUBECONFIG'] = "/tmp/kube.config"
    run_command(f"aws eks update-kubeconfig --name {cluster_name} --alias {cluster_name} --kubeconfig /tmp/kube.config")
    run_command(f"kubectl config use-context {cluster_name}")


def kubectl_get(model: ResourceModel, sess) -> ProgressEvent    :
    LOG.info('Received model: %s' % json.dumps(model._serialize()))
    if proxy_needed(model.ClusterName, sess):
        resp = proxy_call(model._serialize(), sess)
        LOG.info(resp)
        if 'errorMessage' in resp:
            LOG.error(f'{resp["errorType"]}: {resp["errorMessage"]}')
            LOG.error(f'{resp["stackTrace"]}')
            raise Exception(f'{resp["errorType"]}: {resp["errorMessage"]}')
        return ProgressEvent(
            status=OperationStatus.SUCCESS,
            resourceModel=ResourceModel._deserialize(resp)
        )
    create_kubeconfig(model.ClusterName)
    retry_timeout = 600
    while True:
        try:
            outp = run_command('kubectl get %s -o jsonpath="%s" --namespace %s' % (model.Name, model.JsonPath, model.Namespace))
            break
        except Exception as e:
            if retry_timeout < 1:
                raise
            else:
                LOG.error('Exception: %s' % e, exc_info=True)
                LOG.info("retrying until timeout...")
                time.sleep(5)
                retry_timeout = retry_timeout - 5
    model.Response = outp
    if len(outp.encode('utf-8')) > 1000:
        outp = 'MD5-' + str(md5(outp.encode('utf-8')).hexdigest())
    model.Id = outp
    LOG.info("returning progress...")
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )


def set_id(model: ResourceModel):
    model.Id = hashlib.md5(f'{model.ClusterName}{model.Namespace}{model.Name}{model.JsonPath}'.encode('utf-8')).hexdigest()


@resource.handler(Action.CREATE)
def create_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    LOG.error("create handler invoked")
    model = request.desiredResourceState
    put_function(session, model._serialize())
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )


@resource.handler(Action.UPDATE)
def update_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    LOG.error("update handler invoked")
    model = request.desiredResourceState
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )


@resource.handler(Action.DELETE)
def delete_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    LOG.error("delete handler invoked")
    model = request.desiredResourceState
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )


@resource.handler(Action.READ)
def read_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    LOG.error("read handler invoked yurt!")
    model = request.desiredResourceState
    return kubectl_get(model, session)


@resource.handler(Action.LIST)
def list_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    LOG.error("list handler invoked")
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModels=[],
    )


def proxy_wrap(event, _context):
    model = ResourceModel._deserialize(event)
    progress = kubectl_get(model, boto3.session.Session())
    return progress.resourceModel._serialize()
