---
description: Build a dependency DAG and delegate external-model nodes through managed Pitwall channel calls.
argument-hint: [task to orchestrate as a DAG across external models]
---

First invoke the `subagent-model-routing` skill. Map the task into nodes and dependency edges, then execute only nodes whose prerequisites have completed and whose results have been reviewed.

For every external-model node, launch a `pitwall:<harness>-shim` Agent (background allowed) or call the `pitwall-channel` MCP tool `dispatch_and_wait`. For a shim Agent, the `pitwall-asks` monitor surfaces the node's questions: answer with `answer_ask` (for a paused tier-4 run, answer and then `pitwall agents runs resume <dispatch-id>`). For `dispatch_and_wait`, an ask returns as that tool's result: answer with `answer_and_wait`, and continue a bounded `still_running` result with `wait_dispatch`. Steer either kind with `steer_and_wait`. Record each node's dispatch ID and outcome before releasing dependants. Independent ready nodes may run concurrently when the host supports parallel work.

Do not reach an external node through a raw provider CLI: the launch guard denies recognizable raw provider launches in routing-active sessions. A native `Workflow` may carry shim nodes; each shim connects to this session through the `pitwall-asks` monitor. Native Claude agents are not Pitwall channel children. If neither a shim Agent, a Workflow shim node, nor the managed channel is available for a node, report that limitation and stop that node rather than silently switching transport.

Task: $ARGUMENTS
