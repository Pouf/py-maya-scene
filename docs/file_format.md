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

- `FRDI`: u32, then `path\0namespace\0`, flag bytes, `refNode\0`, padding, the
  `-op` options string (`VERS|...|`), the file type (`mayaBinary`).
- `FREF`: the same without the leading u32.
- The reference node (`REFN` group) holds `REFE` chunks: reference edits.

### Nodes

Each node is a group whose form type is the node's 4-byte type id: readable
tags for built-in types (`XFRM` transform, `DMSH` mesh, `RTFT` file, `AUDI`
audio, `REFN` reference, `SCRP` script), raw ids for plugin types.

`CREA` (first chunk): flag byte, `name\0`, optional `parent\0`, 16-byte UUID.
The parent is a short name, or a DAG path (`|grpA|cube`) when the short name
is not unique. Observed flag bits: `0x80` no parent, `0x01` shared (`-s`),
`0x04` DAG node, `0x08` transform, `0x10` reference node.

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
