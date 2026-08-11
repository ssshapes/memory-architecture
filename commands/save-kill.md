---
name: save-kill
description: Terminal session closure. Runs the full save-clear checklist including the forced episodic capture, then the session ends itself. For finished scoped sessions and roster cleanup. Refuses on standing channels without explicit confirmation.
---

# /save-kill

The terminal sibling of `/save-clear`: same save-everything, but the session ends itself instead of waiting for you to type `/clear`. Use it for finished scoped sessions, retiring ephemeral ones, and any "we are done here, close it out."

Assumes sessions run inside tmux. If yours do not, the checklist still applies and only the kill mechanics change.

## Guard, first, always

- Identify self: `S=$(tmux display-message -p '#S')`.
- **If this is a standing channel, stop and ask.** Long-lived main sessions do not die casually; everything else may.
- If there is no tmux session name, fall back to `/save-clear` behaviour and say the session has to be closed by hand.

## Steps 1 to 5: run /save-clear in full

The entire checklist: the sweep, the consolidation review, ledger verification, the **forced episodic capture**, the manifest. The capture is non-negotiable here. This session is about to die, so the SessionEnd hook is belt-and-braces, not the plan.

## Step 6: deregister from any respawn roster, BEFORE arming the timer

If you have anything that resurrects sessions after a reboot (a resume script, a supervisor, a login hook), a killed session that is still listed **comes back on the next reboot with its full context restored**. Observed, not hypothetical: a session killed cleanly at 23:36 was resurrected at 23:43 by a reboot self-heal. The kill timer had worked perfectly.

Killing without deregistering is not a death, it is a nap.

Comment the entry out with a dated reason rather than deleting it. The session id is the only pointer back to the conversation if it is ever needed. Standing channels stay in the roster; they are supposed to come back.

## Step 7: the kill sequence

Only after the manifest is written into the response, and step 6 is done:

```bash
S=$(tmux display-message -p '#S')
nohup sh -c "sleep 8; tmux kill-session -t '$S'" >/dev/null 2>&1 &
echo "kill timer armed for $S"
```

The delay lets the final message finish streaming before the session dies. Background timers survive the tool call, so if a session outlives a `/save-kill`, suspect the roster (step 6), not the timer.

Then end the response with the manifest verdict and one line: **kill timer armed, this session ends in 8 seconds.** Nothing after that. There is no after that.

**Order discipline:** manifest, then deregister, then arm. Arming first means the session can die with its closure report unwritten.

## Notes

- Killing cold, with no save, is fine for misfired sessions that contain nothing. This command is for sessions that lived.
- The raw transcript survives any death. The forced capture is what makes the life retrievable.
