---
description: Build a dependency DAG and delegate external-model nodes through managed Pitwall channel calls.
argument-hint: [task to orchestrate as a DAG across external models]
---

First invoke the `subagent-model-routing` skill. Map the task into nodes and dependency edges, then execute only nodes whose prerequisites have completed and whose results have been reviewed.

For every external-model node, use the `pitwall-channel` MCP tool `dispatch_and_wait`. An ask returns to you as that tool's result; answer it with `answer_and_wait`. A bounded `still_running` result requires `wait_dispatch` with the returned dispatch ID. Use `steer_and_wait` for mid-run direction. Record each node's dispatch ID and terminal outcome before releasing dependent nodes. Independent ready nodes may run concurrently through separate managed calls when the host supports parallel tool calls.

Do not launch an external node through a shim `Agent`, direct shim/provider shell command, backgrounded Bash call, or native `Workflow` carrying shim agent types. Those paths do not establish a live ask-to-parent exchange. Native Claude agents are not Pitwall channel children; completion notifications alone do not satisfy this command's communication requirement. If the managed channel is unavailable for a node, report that limitation and stop that node rather than silently switching transport.

Task: $ARGUMENTS
