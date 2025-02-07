import time
from collections import defaultdict

from fixtures.neon_fixtures import (
    NeonEnvBuilder,
)
from fixtures.pageserver.http import PageserverHttpClient
from fixtures.pageserver.utils import tenant_delete_wait_completed, timeline_delete_wait_completed
from fixtures.pg_version import PgVersion
from fixtures.types import TenantId, TimelineId
from fixtures.utils import wait_until


def test_sharding_service_smoke(
    neon_env_builder: NeonEnvBuilder,
):
    """
    Test the basic lifecycle of a sharding service:
    - Restarting
    - Restarting a pageserver
    - Creating and deleting tenants and timelines
    - Marking a pageserver offline
    """

    neon_env_builder.num_pageservers = 3
    env = neon_env_builder.init_configs()

    # Start services by hand so that we can skip a pageserver (this will start + register later)
    env.broker.try_start()
    env.attachment_service.start()
    env.pageservers[0].start()
    env.pageservers[1].start()
    for sk in env.safekeepers:
        sk.start()

    # The pageservers we started should have registered with the sharding service on startup
    nodes = env.attachment_service.node_list()
    assert len(nodes) == 2
    assert set(n["node_id"] for n in nodes) == {env.pageservers[0].id, env.pageservers[1].id}

    # Starting an additional pageserver should register successfully
    env.pageservers[2].start()
    nodes = env.attachment_service.node_list()
    assert len(nodes) == 3
    assert set(n["node_id"] for n in nodes) == {ps.id for ps in env.pageservers}

    # Use a multiple of pageservers to get nice even number of shards on each one
    tenant_shard_count = len(env.pageservers) * 4
    tenant_count = len(env.pageservers) * 2
    shards_per_tenant = tenant_shard_count // tenant_count
    tenant_ids = set(TenantId.generate() for i in range(0, tenant_count))

    # Creating several tenants should spread out across the pageservers
    for tid in tenant_ids:
        env.neon_cli.create_tenant(tid, shard_count=shards_per_tenant)

    def get_node_shard_counts():
        counts: defaultdict[str, int] = defaultdict(int)
        for tid in tenant_ids:
            for shard in env.attachment_service.locate(tid):
                counts[shard["node_id"]] += 1
        return counts

    for node_id, count in get_node_shard_counts().items():
        # we used a multiple of pagservers for the total shard count,
        # so expect equal number on all pageservers
        assert count == tenant_shard_count / len(
            env.pageservers
        ), f"Node {node_id} has bad count {count}"

    # Creating and deleting timelines should work, using identical API to pageserver
    timeline_crud_tenant = next(iter(tenant_ids))
    timeline_id = TimelineId.generate()
    env.attachment_service.pageserver_api().timeline_create(
        pg_version=PgVersion.NOT_SET, tenant_id=timeline_crud_tenant, new_timeline_id=timeline_id
    )
    timelines = env.attachment_service.pageserver_api().timeline_list(timeline_crud_tenant)
    assert len(timelines) == 2
    assert timeline_id in set(TimelineId(t["timeline_id"]) for t in timelines)
    #    virtual_ps_http.timeline_delete(tenant_id=timeline_crud_tenant, timeline_id=timeline_id)
    timeline_delete_wait_completed(
        env.attachment_service.pageserver_api(), timeline_crud_tenant, timeline_id
    )
    timelines = env.attachment_service.pageserver_api().timeline_list(timeline_crud_tenant)
    assert len(timelines) == 1
    assert timeline_id not in set(TimelineId(t["timeline_id"]) for t in timelines)

    # Marking a pageserver offline should migrate tenants away from it.
    env.attachment_service.node_configure(env.pageservers[0].id, {"availability": "Offline"})

    def node_evacuated(node_id: int):
        counts = get_node_shard_counts()
        assert counts[node_id] == 0

    wait_until(10, 1, lambda: node_evacuated(env.pageservers[0].id))

    # Marking pageserver active should not migrate anything to it
    # immediately
    env.attachment_service.node_configure(env.pageservers[0].id, {"availability": "Active"})
    time.sleep(1)
    assert get_node_shard_counts()[env.pageservers[0].id] == 0

    # Delete all the tenants
    for tid in tenant_ids:
        tenant_delete_wait_completed(env.attachment_service.pageserver_api(), tid, 10)

    # Set a scheduling policy on one node, create all the tenants, observe
    # that the scheduling policy is respected.
    env.attachment_service.node_configure(env.pageservers[1].id, {"scheduling": "Draining"})

    # Create some fresh tenants
    tenant_ids = set(TenantId.generate() for i in range(0, tenant_count))
    for tid in tenant_ids:
        env.neon_cli.create_tenant(tid, shard_count=shards_per_tenant)

    counts = get_node_shard_counts()
    # Nothing should have been scheduled on the node in Draining
    assert counts[env.pageservers[1].id] == 0
    assert counts[env.pageservers[0].id] == tenant_shard_count // 2
    assert counts[env.pageservers[2].id] == tenant_shard_count // 2


def test_sharding_service_passthrough(
    neon_env_builder: NeonEnvBuilder,
):
    """
    For simple timeline/tenant GET APIs that don't require coordination across
    shards, the sharding service implements a proxy to shard zero.  This test
    calls those APIs.
    """
    neon_env_builder.num_pageservers = 2
    env = neon_env_builder.init_start()

    # We will talk to attachment service as if it was a pageserver, using the pageserver
    # HTTP client
    client = PageserverHttpClient(env.attachment_service_port, lambda: True)
    timelines = client.timeline_list(tenant_id=env.initial_tenant)
    assert len(timelines) == 1


def test_sharding_service_restart(neon_env_builder: NeonEnvBuilder):
    env = neon_env_builder.init_start()
    tenant_a = env.initial_tenant
    tenant_b = TenantId.generate()
    env.attachment_service.tenant_create(tenant_b)
    env.pageserver.tenant_detach(tenant_a)

    # TODO: extend this test to use multiple pageservers, and check that locations don't move around
    # on restart.

    # Attachment service restart
    env.attachment_service.stop()
    env.attachment_service.start()

    observed = set(TenantId(tenant["id"]) for tenant in env.pageserver.http_client().tenant_list())

    # Tenant A should still be attached
    assert tenant_a not in observed

    # Tenant B should remain detached
    assert tenant_b in observed

    # Pageserver restart
    env.pageserver.stop()
    env.pageserver.start()

    # Same assertions as above: restarting either service should not perturb things
    observed = set(TenantId(tenant["id"]) for tenant in env.pageserver.http_client().tenant_list())
    assert tenant_a not in observed
    assert tenant_b in observed


def test_sharding_service_onboarding(
    neon_env_builder: NeonEnvBuilder,
):
    """
    We onboard tenants to the sharding service by treating it as a 'virtual pageserver'
    which provides the /location_config API.  This is similar to creating a tenant,
    but imports the generation number.
    """

    neon_env_builder.num_pageservers = 2

    # Start services by hand so that we can skip registration on one of the pageservers
    env = neon_env_builder.init_configs()
    env.broker.try_start()
    env.attachment_service.start()

    # This is the pageserver where we'll initially create the tenant
    env.pageservers[0].start(register=False)
    origin_ps = env.pageservers[0]

    # This is the pageserver managed by the sharding service, where the tenant
    # will be attached after onboarding
    env.pageservers[1].start(register=True)
    dest_ps = env.pageservers[1]
    virtual_ps_http = PageserverHttpClient(env.attachment_service_port, lambda: True)

    for sk in env.safekeepers:
        sk.start()

    # Create a tenant directly via pageserver HTTP API, skipping the attachment service
    tenant_id = TenantId.generate()
    generation = 123
    origin_ps.http_client().tenant_create(tenant_id, generation=generation)

    # As if doing a live migration, first configure origin into stale mode
    origin_ps.http_client().tenant_location_conf(
        tenant_id,
        {
            "mode": "AttachedStale",
            "secondary_conf": None,
            "tenant_conf": {},
            "generation": generation,
        },
    )

    # Call into attachment service to onboard the tenant
    generation += 1
    virtual_ps_http.tenant_location_conf(
        tenant_id,
        {
            "mode": "AttachedMulti",
            "secondary_conf": None,
            "tenant_conf": {},
            "generation": generation,
        },
    )

    # As if doing a live migration, detach the original pageserver
    origin_ps.http_client().tenant_location_conf(
        tenant_id,
        {
            "mode": "Detached",
            "secondary_conf": None,
            "tenant_conf": {},
            "generation": None,
        },
    )

    # As if doing a live migration, call into the attachment service to
    # set it to AttachedSingle: this is a no-op, but we test it because the
    # cloud control plane may call this for symmetry with live migration to
    # an individual pageserver
    virtual_ps_http.tenant_location_conf(
        tenant_id,
        {
            "mode": "AttachedSingle",
            "secondary_conf": None,
            "tenant_conf": {},
            "generation": generation,
        },
    )

    # We should see the tenant is now attached to the pageserver managed
    # by the sharding service
    origin_tenants = origin_ps.http_client().tenant_list()
    assert len(origin_tenants) == 0
    dest_tenants = dest_ps.http_client().tenant_list()
    assert len(dest_tenants) == 1
    assert TenantId(dest_tenants[0]["id"]) == tenant_id

    # sharding service advances generation by 1 when it first attaches
    assert dest_tenants[0]["generation"] == generation + 1

    # The onboarded tenant should survive a restart of sharding service
    env.attachment_service.stop()
    env.attachment_service.start()

    # The onboarded tenant should surviev a restart of pageserver
    dest_ps.stop()
    dest_ps.start()
