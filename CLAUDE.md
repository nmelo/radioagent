# agent-radio

## Project Documents

| Document | Question | Contains |
|----------|----------|----------|
| `docs/prd.md` | **Why** does this exist? | Problem statement, users, success criteria, journeys |
| `docs/spec.md` | **What** does this do? | Requirements, behaviors, acceptance criteria |
| `docs/systemdesign.md` | **How** does this work? | Architecture, packages, interfaces, build order |
| `docs/roadmap.md` | **When/Who** does what get built? | Phases, milestones, gates, agent allocation |

## Roles

- `super/` - super agent directory
- `pm/` - pm agent directory
- `eng1/` - eng1 agent directory
- `eng2/` - eng2 agent directory
- `qa1/` - qa1 agent directory
- `shipper/` - shipper agent directory

## Issue Tracking

Uses beads (`bd` CLI). All work is tracked as beads.

```bash
bd ready            # See unblocked work
bd list             # See all beads
bd show <id>        # Bead details
bd update <id> --status <status>  # Transition bead
```

## Communication

```bash
initech send <agent> "message"   # Send message to an agent
initech peek <agent>              # Read agent terminal output
```


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
