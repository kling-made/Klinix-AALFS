# Build recipe format

A catalog import holds an exact LFS Git object ID and a systemd edition label. Each recipe has a fault-diagnostic source path, the stable ID `chapterNN/section-name`, a chapter and sequence, an optional book product and version, zero or more material references, reference steps, and typed dependency edges.

A reference step records DocBook `screen/userinput` text, command labels, and whether the book presents it as a manual or conditional step. LFS contains examples, placeholders, and branches whose interpretation requires human review. The planner emits structured JSON with phase and provenance for each recipe, and marks all command blocks as reference data.

For LFS bootstrap, distinct IDs preserve the same package's passes and environments. Required edges encode the included sections' sequence. Package-name lookup selects the last matching recipe in book order. An exact ID selects the requested stage. Some materials, such as archives named using shell globs, require future reviewed adapters. The material inventory contains the full available Chapter 3 catalog.

The generic resolver accepts required, recommended, and optional edges. It validates every reference and rejects cycles among edges enabled by the selected policy. Changing the policy changes which additional recipes are selected.
