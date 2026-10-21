---
description: Build a dependency DAG and delegate external-model nodes through managed Pitwall channel calls.
argument-hint: [task to orchestrate as a DAG across external models]
---

First invoke the `subagent-model-routing` skill. Map the task into nodes and dependency edges, then execute only nodes whose prerequisites have completed and whose results have been reviewed.

For every external-model node, launch a `pitwall:<harness>-shim` Agent (background allowed) or call the `pitwall-channel` MCP tool `dispatch_and_wait`. For a shim Agent, the `pitwall-asks` monitor surfaces the node's questions: answer with `answer_ask` (for a paused tier-4 run, answer and then `pitwall agents runs resume <dispatch-id>`). For `dispatch_and_wait`, an ask returns as that tool's result: answer with `answer_and_wait`, and continue a bounded `still_running` result with `wait_dispatch`. Steer either kind with `steer_and_wait`. Record each node's dispatch ID and outcome before releasing dependants. Independent ready nodes may run concurrently when the host supports parallel work.

Do not reach an external node through a raw provider CLI or a native `Workflow` script carrying shim nodes: the launch guard denies both in a routing-active session. Native Claude agents are not Pitwall channel children. If neither a shim Agent nor the managed channel is available for a node, report that limitation and stop that node rather than silently switching transport.

Task: $ARGUMENTS
