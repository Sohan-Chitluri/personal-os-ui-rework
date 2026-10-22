#!/usr/bin/env python3.11
"""Worker heartbeat publisher (M1-D).

Deployed on the worker instance as part of the wheel; called by
deploy/systemd/pos-worker.service (via a small --exec wrapper inside the
worker runner) once per loop iteration. Publishes a PersonalOS/<env>
WorkerHeartbeat metric so the CloudWatch alarm can detect a wedged or dead
worker.

Application contract (does NOT modify worker logic): the worker runner is
expected to import and call ``emit_heartbeat()`` from its run loop, or a
sidecar timer can call this module. Until the worker runner lands (see
prerequisites in docs/DEPLOYMENT.md), this module stands alone.
"""

from __future__ import annotations

import os

import boto3


def emit_heartbeat() -> None:
    """Publish one WorkerHeartbeat=1 datapoint."""
    environment = os.environ.get("ENVIRONMENT", "staging")
    boto3.client("cloudwatch").put_metric_data(
        Namespace=f"PersonalOS/{environment}",
        MetricData=[{"MetricName": "WorkerHeartbeat", "Value": 1, "Unit": "Count"}],
    )
