import logging
from typing import Any, MutableMapping, Optional
import json
import subprocess
import shlex
import re
import requests
from ruamel import yaml
from datetime import date, datetime
from time import sleep
import os

import boto3

from cloudformation_cli_python_lib import (
    Action,
    OperationStatus,
    ProgressEvent,
    Resource,
    SessionProxy,
)

from .models import ResourceHandlerRequest, ResourceModel
from .vpc import proxy_needed, proxy_call, put_function

# Use this logger to forward log messages to CloudWatch Logs.
LOG = logging.getLogger(__name__)
TYPE_NAME = "AWSQS::Kubernetes::Resource"
LOG.setLevel(logging.DEBUG)

resource = Resource(TYPE_NAME, ResourceModel)
test_entrypoint = resource.test_entrypoint

s3_scheme = re.compile(r'^s3://.+/.+')


@resource.handler(Action.CREATE)
def create_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    model = request.desiredResourceState
    progress: ProgressEvent = ProgressEvent(
        status=OperationStatus.IN_PROGRESS,
        resourceModel=model,
    )
    physical_resource_id,  manifest_file = handler_init(model, session, request.logicalResourceIdentifier)
    if 'stabilizing' in callback_context:
        if callback_context['stabilizing'].startswith('/apis/batch') and 'cronjobs' not in callback_context['stabilizing']:
            if stabilize_job(model.Namespace, callback_context['name'], model.ClusterName, session):
                progress.status = OperationStatus.SUCCESS
            progress.callbackContext = callback_context
            progress.callbackDelaySeconds = 30
            return progress
    outp = run_command("kubectl create --save-config -o json -f %s -n %s" % (manifest_file, model.Namespace), model.ClusterName, session)
    build_model(json.loads(outp), model)
    if model.SelfLink.startswith('/apis/batch'):
        if not stabilize_job(model.Namespace, model.Name, model.ClusterName, session):
            callback_context['stabilizing'] = model.SelfLink
            callback_context['name'] = model.Name
            progress.callbackContext = callback_context
            progress.callbackDelaySeconds = 30
            return progress
    progress.status = OperationStatus.SUCCESS
    return progress


@resource.handler(Action.UPDATE)
def update_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    model = request.desiredResourceState
    progress: ProgressEvent = ProgressEvent(
        status=OperationStatus.IN_PROGRESS,
        resourceModel=model,
    )
    physical_resource_id, manifest_file = handler_init(model, session, request.logicalResourceIdentifier)
    if 'stabilizing' in callback_context:
        if callback_context['stabilizing'].startswith('/apis/batch'):
            if stabilize_job(model.Namespace, callback_context['name']):
                progress.status = OperationStatus.SUCCESS
            progress.callbackContext = callback_context
            progress.callbackDelaySeconds = 30
            return progress
    outp = run_command("kubectl apply -o json -f %s -n %s" % (manifest_file, model.Namespace), model.ClusterName, session)
    build_model(json.loads(outp), model)
    if model.SelfLink.startswith('/apis/batch'):
        if not stabilize_job(model.Namespace, model.Name):
            callback_context['stabilizing'] = model.SelfLink
            callback_context['name'] = model.Name
            progress.callbackContext = callback_context
            progress.callbackDelaySeconds = 30
            return progress
    progress.status = OperationStatus.SUCCESS
    return progress


@resource.handler(Action.DELETE)
def delete_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    model = request.desiredResourceState
    progress: ProgressEvent = ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )
    physical_resource_id, manifest_file = handler_init(model, session, request.logicalResourceIdentifier)
    run_command("kubectl delete -f %s -n %s" % (manifest_file, model.Namespace), model.ClusterName, session)
    return progress


@resource.handler(Action.READ)
def read_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    model = request.desiredResourceState
    if not proxy_needed(model.ClusterName, session):
        create_kubeconfig(model.ClusterName)
    namespace, kind, name = tuple(model.SelfLink.split('/')[-3:])
    outp = run_command(f"kubectl get {kind}/{name} -n {namespace} -o json", model.ClusterName, session)
    build_model(json.loads(outp), model)
    return ProgressEvent(
        status=OperationStatus.SUCCESS,
        resourceModel=model,
    )


@resource.handler(Action.LIST)
def list_handler(
    session: Optional[SessionProxy],
    request: ResourceHandlerRequest,
    callback_context: MutableMapping[str, Any],
) -> ProgressEvent:
    raise NotImplementedError("List handler not implemented.")


def s3_get(url, s3_client):
    try:
        return s3_client.get_object(
            Bucket=url.split('/')[2], Key="/".join(url.split('/')[3:])
        )['Body'].read().decode('utf8')
    except Exception as e:
        raise RuntimeError(f"Failed to fetch CustomValueYaml {url} from S3. {e}")


def http_get(url):
    try:
        response = requests.get(url)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to fetch CustomValueYaml url {url}: {e}")
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch CustomValueYaml url {url}: [{response.status_code}] "
            f"{response.reason}"
        )
    return response.text


def run_command(command, cluster_name, session):
    if cluster_name and session:
        if proxy_needed(cluster_name, session):
            put_function(session, cluster_name)
            with open('/tmp/manifest.json', 'r') as fh:
                resp = proxy_call(cluster_name, fh.read(), command, session)
            LOG.info(resp)
            return resp
    retries = 0
    while True:
        try:
            try:
                LOG.debug("executing command: %s" % command)
                output = subprocess.check_output(shlex.split(command), stderr=subprocess.STDOUT).decode("utf-8")
                LOG.debug(output)
            except subprocess.CalledProcessError as exc:
                LOG.error("Command failed with exit code %s, stderr: %s" % (exc.returncode,
                                                                               exc.output.decode("utf-8")))
                raise Exception(exc.output.decode("utf-8"))
            return output
        except Exception as e:
            if 'Unable to connect to the server' not in str(e) or retries >= 5:
                raise
            LOG.debug("{}, retrying in 5 seconds".format(e))
            sleep(5)
            retries += 1


def create_kubeconfig(cluster_name):
    os.environ['PATH'] = f"/var/task/bin:{os.environ['PATH']}"
    os.environ['PYTHONPATH'] = f"/var/task:{os.environ.get('PYTHONPATH', '')}"
    os.environ['KUBECONFIG'] = "/tmp/kube.config"
    run_command(f"aws eks update-kubeconfig --name {cluster_name} --alias {cluster_name} --kubeconfig /tmp/kube.config", None, None)
    run_command(f"kubectl config use-context {cluster_name}", None, None)


def json_serial(o):
    if isinstance(o, (datetime, date)):
        return o.strftime('%Y-%m-%dT%H:%M:%SZ')
    raise TypeError("Object of type '%s' is not JSON serializable" % type(o))


def write_manifest(manifest, path):
    f = open(path, "w")
    if isinstance(manifest, dict):
        manifest = json.dumps(manifest, default=json_serial)
    f.write(manifest)
    f.close()


def generate_name(model, physical_resource_id, stack_name):
    manifest = yaml.safe_load(model.Manifest)
    if "metadata" in manifest.keys():
        if 'name' not in manifest["metadata"].keys() and 'generateName' not in manifest["metadata"].keys():
            if physical_resource_id:
                manifest["metadata"]["name"] = physical_resource_id.split('/')[-1]
            else:
                manifest["metadata"]["generateName"] = "cfn-%s-" % stack_name.lower()
    return manifest


def build_model(kube_response, model):
    for key in ["uid", "selfLink", "resourceVersion", "namespace", "name"]:
        if key in kube_response["metadata"].keys():
            setattr(model, key[0].capitalize() + key[1:], kube_response["metadata"][key])


def handler_init(model, session, stack_name):
    LOG.debug('Received model: %s' % json.dumps(model._serialize(), default=json_serial))

    physical_resource_id = None
    manifest_file = None
    if not proxy_needed(model.ClusterName, session):
        create_kubeconfig(model.ClusterName)
    s3_client = session.client('s3')
    if (not model.Manifest and not model.Url) or (model.Manifest and model.Url):
        raise Exception("Either Manifest or Url must be specified.")
    if model.Manifest:
        manifest_file = '/tmp/manifest.json'
        if model.SelfLink:
            physical_resource_id = model.SelfLink
        manifest = generate_name(model, physical_resource_id, stack_name)
        write_manifest(manifest, manifest_file)
        LOG.debug("Applying manifest: %s" % json.dumps(manifest, default=json_serial))
    elif model.Url:
        manifest_file = '/tmp/manifest.json'
        if re.match(s3_scheme, model.Url):
            response = s3_get(model.Url, s3_client)
        else:
            response = http_get(model.Url)
        manifest = yaml.safe_load(response)
        write_manifest(manifest, manifest_file)
    return physical_resource_id, manifest_file


def stabilize_job(namespace, name, cluster_name, session):
    response = json.loads(run_command(f"kubectl get job/{name} -n {namespace} -o json", cluster_name, session))
    for condition in response.get('status', {}).get('conditions', []):
        if condition.get("status") == "True":
            if condition.get('type') == "Complete":
                return True
            if condition.get('type') == "Failed":
                raise Exception(f"Job failed {condition.get('reason')} {condition.get('message')}")
    return False


def proxy_wrap(event, _context):
    LOG.debug(json.dumps(event))
    if event.get('manifest'):
        write_manifest(event['manifest'], '/tmp/manifest.json')
    create_kubeconfig(event['cluster_name'])
    return run_command(event['command'], event['cluster_name'], boto3.session.Session())
