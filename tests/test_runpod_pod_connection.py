"""The raw-pod view carries the pod's public address so an agent can reach its own pod."""

from __future__ import annotations

from pitwall.runpod_control_plane import CARRIER_GRADE_NAT, _pod_resource


def test_rest_pod_exposes_public_ip_and_port_mappings() -> None:
    pod = _pod_resource(
        {
            "id": "0xzp9kwv4wtgb4",
            "name": "p",
            "desiredStatus": "RUNNING",
            "publicIp": "203.0.113.7",
            "portMappings": {"22": 22060},
        }
    )
    assert (pod.public_ip, pod.port_mappings) == ("203.0.113.7", {"22": 22060})
    assert pod.model_dump(mode="json")["port_mappings"] == {"22": 22060}


def test_graphql_runtime_public_ports_are_used_when_rest_fields_are_absent() -> None:
    pod = _pod_resource(
        {
            "id": "abc123def456gh",
            "desiredStatus": "RUNNING",
            "runtime": {
                "ports": [
                    {
                        "ip": "203.0.113.8",
                        "isIpPublic": True,
                        "privatePort": 22,
                        "publicPort": 40022,
                        "type": "tcp",
                    },
                    {
                        "ip": "10.0.0.2",
                        "isIpPublic": False,
                        "privatePort": 8000,
                        "publicPort": 8000,
                        "type": "http",
                    },
                ]
            },
        }
    )
    assert (pod.public_ip, pod.port_mappings) == ("203.0.113.8", {"22": 40022})


def test_malformed_mappings_are_ignored_not_raised() -> None:
    pod = _pod_resource(
        {
            "id": "abc123def456gh",
            "desiredStatus": "RUNNING",
            "portMappings": {"ssh": "x", "22": None, "8888": 30888},
        }
    )
    assert pod.port_mappings == {"8888": 30888}


def test_pod_without_network_fields_is_unchanged() -> None:
    pod = _pod_resource({"id": "abc123def456gh", "desiredStatus": "EXITED"})
    assert (pod.public_ip, pod.port_mappings) == (None, {})


def test_rest_v2_runtime_ports_shape_from_the_live_broker() -> None:
    # The broker's RunPod client returns runtime.ports entries as {ip, private, public, type}
    # with no isIpPublic flag; the HTTP proxy entry carries a carrier-grade NAT address.
    pod = _pod_resource(
        {
            "id": "pzcyf7e2w2e8o8",
            "desiredStatus": "RUNNING",
            "ports": ["22/tcp"],
            "runtime": {
                "ports": [
                    {"ip": "203.0.113.52", "private": 22, "public": 22052, "type": "tcp"},
                    {
                        "ip": str(CARRIER_GRADE_NAT[0x112E7]),
                        "private": 19123,
                        "public": 60495,
                        "type": "http",
                    },
                ]
            },
        }
    )
    assert (pod.public_ip, pod.port_mappings) == ("203.0.113.52", {"22": 22052})


def test_private_and_loopback_addresses_are_not_public() -> None:
    for internal in (
        "10.1.2.3",
        "172.20.0.5",
        "192.168.1.9",
        "127.0.0.1",
        "169.254.1.1",
        str(CARRIER_GRADE_NAT[1]),
    ):
        pod = _pod_resource(
            {
                "id": "abc123def456gh",
                "desiredStatus": "RUNNING",
                "runtime": {
                    "ports": [{"ip": internal, "private": 22, "public": 40022, "type": "tcp"}]
                },
            }
        )
        assert (pod.public_ip, pod.port_mappings) == (None, {}), internal
