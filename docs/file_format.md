# File Format

What is known of Maya scene files, learned from files Maya writes: fixtures
saved by Maya 2018, 2022 and 2024 (`samples/models/`), each scene saved as
both `.mb` and `.ma`, and a read-only sweep of 4076 production files (771
`.mb`, 3305 `.ma`). "Observed" marks rules seen in every file so far but not
yet probed further.

## Maya Binary (`.mb`)

### Container

`.mb` is an IFF variant, 64-bit flavour (`FOR8`) for every Maya 2018 to 2025
file. Values are big-endian.

| Part | Bytes | Notes |
|---|---|---|
| tag | 4 | `FOR8`, `LIS8`, `CAT8`, `PRO8` for groups, any 4 bytes for data |
| reserved | 4 | Not initialised by Maya and ignored on read (zeroed copies open in Maya 2024) |
| size | 8 | u64. Excludes the header and any padding |
| form type | 4 | Groups only: the first 4 bytes of the body |

- Data chunks are padded with NUL bytes to a multiple of 8, counted from the
  start of their data. Children of a group start right after its form type, so
  chunk starts are usually *not* 8-byte aligned in the file.
- Groups are never padded: their size is always 4 + the extents of their
  children.

### Layout

```
FOR8 Maya
  FOR8 HEAD            header
  FOR8 FRDI            one per reference (file -rdi)
  FOR8 FREF            one per reference (file -r)
  FOR8 <type id>       one per created node
  FOR8 SLCT            one per default node with edited attributes (select -ne)
  LIS8 CONS
    FOR8 CONN          connections and relationships
  FOR8 HEAD            trailer
```

### Header (`HEAD`)

| Chunk | Content | `.ma` twin |
|---|---|---|
| `VERS` | Maya version (`2022`, `2018ff09` for Maya 2019) | `requires maya` |
| `UVER`, `MADE`, `ICON`, `INFO` | Strings, `undef` so far | |
| `CHNG` | Save date | `//Last modified:` |
| `OBJN`, `INCL` | Object count, reference paths | |
| `LUNI`, `TUNI`, `AUNI` | Linear, time and angle units | `currentUnit` |
| `TDUR` | 141120000 (ticks per second) | |
| `FINF` | `key\0value\0` | `fileInfo` |
| `PLUG` | `plugin\0version\0requires flags\0` then repeated `nodeTypeName\0` + 4-byte type id | `requires` |

`PLUG` maps plugin node type ids to names, so plugin nodes can be named without
the plugin. Two production type ids (`0x58000300`, `0x58000301`) are not
listed by any `PLUG` chunk of their files.

### References

- `FRDI` (`file -rdi`): one per reference at every depth, the references of
  referenced files included. A u32 depth (1 for this file's own references),
  then `path\0namespace\0`, a flag byte (1 so far), a deferred flag (1 for
  `-dr 1`), `refNode\0`, padding, the `-op` options string, the file type
  (`mayaBinary`, `mayaAscii`).
- `FREF` (`file -r`): one per top-level reference, the same layout without the
  depth. Its deferred flag is always 1, like the `-dr 1` of every `file -r`
  line; only `FRDI` tells deferred references apart.
- Paths are stored unresolved: environment variables kept, and no `{n}` copy
  number for a file referenced twice (Maya adds it on load).
- The `-op` string caches the referenced file's own header, its `INCL`
  references included. Deferred references have none.
- The trailer `INCL` lists the top-level reference paths, in `FREF` order.
- The reference node (`REFN` group) holds a `REFE` chunk (its `ed`
  attribute): the reference edits, see below.

### Reference edits (`REFE`)

The value (after `ed\0` and the flag byte) is the owning reference node name,
then two sections with the same layout: a u32 group count, and per group a
reference node name, a u32 edit count and the edits. Fixtures always leave the
first section's groups empty; production files sometimes put parent edits
there. Each edit is a u32 kind and NUL-terminated strings, as in the
`dataReferenceEdits` lines of `.ma` files:

| Kind | `.ma` command | Strings |
|---|---|---|
| 0 | `parent` | node, new parent, flags (`-s -r `) |
| 1 | `addAttr` | node, long name, short name, arguments |
| 2 | `setAttr` | node, attribute, MEL arguments (` -type "string" "P:/a.exr"`) |
| 3 | `disconnectAttr` | source plug, destination plug, flags (`-na`) |
| 4 | `deleteAttr` | node, attribute, flags |
| 5 | `connectAttr` | u32 side, then: side 3 or 4 (the referenced plug is the source or the destination): reference node, plug, placeholder, extra; side 0 (both ends referenced): reference node, source, destination, two placeholders, extra |
| 8 | lock attribute | node, attribute |
| 9 | unlock attribute | node, attribute |

Attribute names in edits are **long** names. Nodes are full DAG paths, which
include the local nodes a referenced node was parented under
(`|localGrp|e:refCube`). A connection between a local node and a referenced
node is stored twice: as a plain `CWFL` to the reference node's placeholder
list (`aRN.phl[1]`) and as a kind 5 edit mapping the placeholder to the
referenced plug. Parenting a local node under a referenced one creates a local
`<refNode>fosterParent1` node and a kind 0 edit. Locking a node is not an
edit. All 786 production `.mb` files decode with these rules and re-encode
byte for byte (260k edits).

### Nodes

Each node is a group whose form type is the node's 4-byte type id: readable
tags for built-in types (`XFRM` transform, `DMSH` mesh, `RTFT` file, `AUDI`
audio, `REFN` reference, `SCRP` script), raw ids for plugin types.

`CREA` (first chunk): flag byte, `name\0`, optional `parent\0`, 16-byte UUID.
The parent is a short name, or a DAG path (`|grpA|cube`) when the short name
is not unique. Flag bits: `0x04` DAG node and `0x08` transform (checked on
48k nodes of fixtures and production files against the type hierarchy);
observed: `0x80` no parent, `0x01` shared (`-s`), `0x10` reference node.

The UUID's text form (`B6A733A0-4DD6-F4D1-E1A6-3282A7CA548B`) reads the 16
bytes as four little-endian u32 words.

Attribute chunks follow: the tag is the storage type, the data is
`shortName\0` + a flag byte + the value. Tags in production: `DBLE`, `DBL2`,
`DBL3`, `DBL#`, `FLT2`, `FLT3`, `FLT#`, `I32#`, `LNG3`, `STR `, `STR#`,
`MATR`, `CMPD`, `CMP#`, `FLGS`, `MESH`, `NRBC`, `NRBS`, `PNT#`, `VEC#`, `ATTR`
(added attribute), `ATAL` (attribute alias). `DBLE` also stores booleans,
integers, enums and times (as int64 ticks), so decoding a value needs the
attribute's type.

### Selections and connections

- `SLCT` group: a `SLCT` chunk with the node name (`:time1`, no NUL), then
  attribute chunks, like `select -ne` followed by `setAttr` in `.ma`.
- `CWFL`: flag byte (`0x01` = `-na`), `source plug\0destination plug\0`.
  Plugs use the shortest unique node name: `grpA.tx`, `|grpB|cube.r`,
  `ns:target.t`.
- `RELA`: `kind\0node\0`, u32 count, then that many plugs.

### Trailer (`HEAD`)

`OBJN`, `INCL`, then `DATS` + `SMTD` (metadata, see below) and `BCSZ`, whose
u32 is the distance in bytes from the start of the trailer group to the `BCSZ`
chunk; `BCSZ` ends the file. Resizing `INCL` or `SMTD` changes that distance;
whether Maya reads it is not probed yet.

## Maya ASCII (`.ma`)

A MEL script with LF line endings, in the code page named by its
`//Codeset:` comment (1252 for every production file). Statements in order:
`file -rdi` / `file -r` per reference, `requires`, `currentUnit`, `fileInfo`,
then `createNode` blocks (with `rename -uid`, `setAttr`, `addAttr`), `select
-ne` blocks for default nodes, `connectAttr` / `relationship`, and an
optional `dataStructure` + `applyMetadata` pair. Some production files lack
the header comments, and some are empty.

## Shared traps

- Text encoding: `.mb` `STR ` values are UTF-8, but `.ma` text and the
  `externalContentTable` of both formats use the code page (`upathcrc` is the
  zlib CRC-32 of the path in that code page).
- `externalContentTable` (node, attribute, path, CRC, role) lists some file
  dependencies, but only in about 69% of production `.mb` and 21% of `.ma`,
  and never Alembic or Arnold nodes. Maya accepts a stale table.
- Maya-native path attributes use forward slashes; plugin attributes store
  what they were given (production Yeti `cfn` values use backslashes).
