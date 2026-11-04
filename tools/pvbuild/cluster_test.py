"""Functional checks performed through Core's kubectl, before and after reboot."""
import json
import shlex
import uuid

IMAGE = 'docker.io/library/busybox@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'
KUBECTL = 'kubectl --kubeconfig=/k0s-access/admin.conf --request-timeout=10s '


def resources(name):
    labels = {'app': name}
    volume = {'name': 'data', 'persistentVolumeClaim': {'claimName': 'data'}}
    container = {'name': 'server', 'image': IMAGE, 'command': ['sh', '-ec',
        'mkdir -p /data/www; test -f /data/www/index.html || echo ' + name + ' > /data/www/index.html; exec httpd -f -p 8080 -h /data/www'],
        'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': name}},
        {'apiVersion': 'v1', 'kind': 'PersistentVolume', 'metadata': {'name': name}, 'spec': {
            'capacity': {'storage': '32Mi'}, 'accessModes': ['ReadWriteOnce'], 'storageClassName': '',
            'persistentVolumeReclaimPolicy': 'Retain', 'hostPath': {'path': '/data/qualification/' + name, 'type': 'DirectoryOrCreate'}}},
        {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'data', 'namespace': name}, 'spec': {
            'accessModes': ['ReadWriteOnce'], 'storageClassName': '', 'volumeName': name,
            'resources': {'requests': {'storage': '32Mi'}}}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'server', 'namespace': name}, 'spec': {
            'replicas': 1, 'selector': {'matchLabels': labels}, 'template': {'metadata': {'labels': labels},
            'spec': {'containers': [container], 'volumes': [volume]}}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'server', 'namespace': name}, 'spec': {
            'selector': labels, 'ports': [{'port': 8080, 'targetPort': 8080}]}},
        {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'client', 'namespace': name}, 'spec': {
            'containers': [{'name': 'client', 'image': IMAGE, 'command': ['sleep', '86400']}]}}
    ]}


def start(request, wait, process):
    wait(process, KUBECTL + 'get --raw=/readyz', 'ok', seconds=900)
    wait(process, KUBECTL + 'wait --for=condition=Ready nodes --all --timeout=5s', 'condition met', seconds=900)
    name = 'qualification-' + uuid.uuid4().hex[:12]
    payload = json.dumps(resources(name))
    result = request('printf %s ' + shlex.quote(payload) + ' | ' + KUBECTL + 'apply -f -')
    if result.returncode: raise RuntimeError('Cluster fixture creation failed: ' + result.stderr)
    check(request, wait, process, name)
    # Replace the initial token with a unique persisted value; an empty volume
    # after reboot must not pass merely because the server recreated its data.
    result = request(KUBECTL + '-n ' + name + ' exec deployment/server -- sh -c ' +
                     shlex.quote('echo persisted-' + name + ' > /data/www/index.html; sync'))
    if result.returncode: raise RuntimeError('Cannot write persistence token')
    return name


def check(request, wait, process, name, persisted=False):
    prefix = KUBECTL + '-n ' + name + ' '
    wait(process, prefix + 'wait --for=condition=Available deployment/server --timeout=5s', 'condition met', seconds=900)
    wait(process, prefix + 'wait --for=condition=Ready pod/client --timeout=5s', 'condition met', seconds=900)
    token = ('persisted-' if persisted else '') + name
    # DNS + service routing + cross-pod traffic + persistent volume contents.
    return wait(process, prefix + 'exec client -- wget -qO- -T 5 http://server.' + name + '.svc.cluster.local:8080', token, seconds=300)


def cleanup(request, name):
    result = request(KUBECTL + 'delete namespace ' + name + ' --wait=false && ' + KUBECTL + 'delete pv ' + name + ' --wait=false')
    if result.returncode: raise RuntimeError('Cluster fixture cleanup failed: ' + result.stderr)
