import os
import kopf
import kubernetes as k
from kubernetes.stream import stream
import yaml
import random
import string
import asyncio
from datetime import datetime
import time


def get_random_string(length):
    # choose from all lowercase letter
    letters = string.ascii_lowercase
    result_str = ''.join(random.choice(letters) for i in range(length))
    print("Random string of length", length, "is:", result_str)
    return result_str
# def create_mariadb(name, namespace):
#     with open(os.path.join(os.path.dirname(__file__), "mariadb/pvc.yaml")) as f:

#     # pvc = client.V1PersistentVolumeClaim(
#     #     client.V1ObjectMeta(name,namespace),
#     #     client.V1PersistentVolumeClaimSpec(
#     #         access_modes = ["ReadWriteOnce"],
#     #         resources = client.V1ResourceRequirements(
#     #             requests = {"storage":"5Gi"}
#     #         )
#     #     )
#     # )


def create_db(spec, name, namespace, logger, **kwargs):
    direc = os.getcwd()
    db = {}
    for f in os.listdir('mariadb'):
        with open(os.path.join(direc, 'mariadb', f)) as file_object:
            db[f.split('.')[0]] = yaml.safe_load(file_object.read().format(
                name=name, mysql_password=get_random_string(8)))

    api = k.client.CoreV1Api()
    app_api = k.client.AppsV1Api()

    kopf.adopt(db['pvc'])
    db_pvc_obj = api.create_namespaced_persistent_volume_claim(
        namespace=namespace,
        body = db['pvc'],
    )
    logger.info(f"PVC child is created: {db_pvc_obj}")

    kopf.adopt(db['secret'])
    db_secret_obj = api.create_namespaced_secret(
        namespace=namespace,
        body=db['secret'],
    )
    logger.info(f"Secret child is created: {db_secret_obj}")

    kopf.adopt(db['deployment'])
    db_deployment_obj = app_api.create_namespaced_deployment(
        namespace=namespace,
        body=db['deployment'],
    )
    logger.info(f"Deployment child is created: {db_deployment_obj}")

    kopf.adopt(db['service'])
    db_service_obj = api.create_namespaced_service(
        namespace=namespace,
        body=db['service'],
    )
    logger.info(f"Service child is created: {db_service_obj}")

    return {
        'db-pvc-name': db_pvc_obj.metadata.name, 
        #'db-pvc-size': db_pvc_obj.spec['resources']['requests']['storage'],
        'db-secret-name': db_secret_obj.metadata.name,
        'db-deployment-name': db_deployment_obj.metadata.name,
        'db-service-name': db_service_obj.metadata.name
        }

def create_nextcloud(spec, name, namespace, logger, **kwargs):
    direc = os.getcwd()
    nextcloud = {}
    for f in os.listdir('nextcloud'):
        with open(os.path.join(direc, 'nextcloud', f)) as file_object:
            nextcloud[f.split('.')[0]] = yaml.safe_load(file_object.read().format(
                name=name, domain=spec['domain'], version=spec['version']))

    api = k.client.CoreV1Api()
    app_api = k.client.AppsV1Api()
    networking_v1_beta1_api = k.client.NetworkingV1beta1Api()

    kopf.adopt(nextcloud['pvc'])
    nextcloud_pvc_obj = api.create_namespaced_persistent_volume_claim(
        namespace=namespace,
        body=nextcloud['pvc'],
    )
    logger.info(f"PVC child is created: {nextcloud_pvc_obj}")

    kopf.adopt(nextcloud['deployment'])
    nextcloud_deployment_obj = app_api.create_namespaced_deployment(
        namespace=namespace,
        body=nextcloud['deployment'],
    )
    logger.info(f"Deployment child is created: {nextcloud_deployment_obj}")

    kopf.adopt(nextcloud['service'])
    nextcloud_service_obj = api.create_namespaced_service(
        namespace=namespace,
        body=nextcloud['service'],
    )
    logger.info(f"Service child is created: {nextcloud_service_obj}")

    kopf.adopt(nextcloud['ingress'])
    nextcloud_ingress_obj = networking_v1_beta1_api.create_namespaced_ingress(
        namespace=namespace,
        body=nextcloud['ingress'],
    )
    logger.info(f"Ingress child is created: {nextcloud_ingress_obj}")

    return {
        'nextcloud-pvc-name': nextcloud_pvc_obj.metadata.name,
        #'nextcloud-pvc-size': nextcloud_pvc_obj.spec.size,
        'nextcloud-deployment-name': nextcloud_deployment_obj.metadata.name,
        'nextcloud-service-name': nextcloud_service_obj.metadata.name,
        'nextcloud-ingress-name': nextcloud_ingress_obj.metadata.name,
        #'nextcloud-ingress-name': nextcloud_ingress_obj.spec.rules[0].host,
    }


async def wait_for_deployment_complete(deployment_name, namespace, timeout=60):
    #api = _get_kube_api(client.ExtensionsV1beta1Api)
    app_api = k.client.AppsV1Api()
    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        response = app_api.read_namespaced_deployment_status(
            deployment_name, namespace)
        s = response.status
        if (s.updated_replicas == response.spec.replicas and
                s.replicas == response.spec.replicas and
                s.available_replicas == response.spec.replicas and
                s.observed_generation >= response.metadata.generation):
            return True
        else:
            print(f'[updated_replicas:{s.updated_replicas},replicas:{s.replicas}'
                  ',available_replicas:{s.available_replicas},observed_generation:{s.observed_generation}] waiting...')

    raise RuntimeError(f'Waiting timeout for deployment {deployment_name}')

# def run_update_job(name,namespace,version):

#     client = k.client
#     job = client.V1Job(
#         metadata = client.V1ObjectMeta(name,namespace),
#         spec = client.V1PodTemplate(),
#     )

@kopf.on.create('nextclouds')
def create_fn(spec, name, namespace, logger, **kwargs):

    db = create_db(spec, name, namespace, logger, **kwargs)
    nc = create_nextcloud(spec, name, namespace, logger, **kwargs)

    # Return status
    return {**nc, **db}


@kopf.on.update('nextclouds')
async def update_fn(spec, status, namespace, logger, **kwargs):

    version = spec.get('version', None)
    if not version:
        raise kopf.PermanentError(f"Nextcloud versio täytyy asettaa. Saatiin versio {version!r}.")

    # Haetaan Nextcloud deploymentin nimi CRD statuksesta
    nextcloud_deployment_name = status['create_fn']['nextcloud-deployment-name']
    # Luodaan patch, joka vaihtaa Docker levykuvan version
    nextcloud_deployment_patch = { 
    'spec': { 
    'template': {
        'spec': {'containers': [{'name': 'nextcloud', 'image': f"nextcloud:{version}"}]}}}}

    # Muutetaan Nextcloud Deployment käyttämään uutta levykuvaa
    app_api = k.client.AppsV1Api()
    nextcloud_deployment_obj = app_api.patch_namespaced_deployment(
        namespace=namespace,
        name=nextcloud_deployment_name,
        body=nextcloud_deployment_patch,
    )
    await wait_for_deployment_complete(nextcloud_deployment_name, namespace)

    update_job_spec = k.client.V1PodTemplateSpec(
        metadata=nextcloud_deployment_obj.spec.template.metadata,
        spec=nextcloud_deployment_obj.spec.template.spec
    )

    update_job_spec.spec.restart_policy = 'Never'
    update_job_spec.spec.containers[0].command = ['echo','hello']
    #update_job_spec.metadata.name = f"update-{nextcloud_deployment_name}-to-{version}"
    print(update_job_spec)
    #print(nextcloud_deployment_obj['spec'])
    update_job = k.client.V1Job(
        metadata=k.client.V1ObjectMeta(
            name=f"update-{nextcloud_deployment_name}-to-{version}"
        ),
        spec=k.client.V1JobSpec(
            template=update_job_spec
        )
    )

    batch_api = k.client.BatchV1Api()
    update_job_obj = batch_api.create_namespaced_job(
    
        namespace=namespace,
        body=update_job
        )

    logger.info(f"Nextcloud update job dispatched {update_job_obj}")
    # logger.info(
    #    f"Nextcloud Deployment is updated: {nextcloud_deployment_obj.spec.template.spec.containers[0].command}")
    # podspec = k.client.V1PodTemplateSpec(
    #     metadata=nextcloud_deployment_obj.spec.template.metadata,
    #     spec = nextcloud_deployment_obj.spec.template.spec
    # )

   
    # podspec.spec.containers[0].command = 'echo hello'
    # logger.info(podspec.spec)
    
