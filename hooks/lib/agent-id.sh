# LoreLake plugin — readable agent ID generator.
# Two-word adjective+noun + HHMMSS suffix for traceability in logs.
# Source this file; then call generate_agent_id.

LLAKE_AGENT_ADJECTIVES=(swift brave calm bold keen wise warm cool wild free bright quiet gentle lazy happy dancing flying running sleeping jumping)
LLAKE_AGENT_NOUNS=(fox owl bear wolf hawk deer hare crow lion whale tiger eagle panda otter raven bunny falcon pirate knight wizard)

generate_agent_id() {
  local adj_idx=$((RANDOM % ${#LLAKE_AGENT_ADJECTIVES[@]}))
  local noun_idx=$((RANDOM % ${#LLAKE_AGENT_NOUNS[@]}))
  # 4-hex-char suffix from $RANDOM (bash 3.2 OK; not cryptographic — collision
  # avoidance only). Combined with HHMMSS, two agents in the same second have
  # 1/65536 collision odds — adequate for a single project's post-merge volume.
  local suffix
  suffix=$(printf '%04x' $((RANDOM % 65536)))
  echo "${LLAKE_AGENT_ADJECTIVES[$adj_idx]}-${LLAKE_AGENT_NOUNS[$noun_idx]}-$(date +%H%M%S)-${suffix}"
}
