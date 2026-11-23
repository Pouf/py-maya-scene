# File Format

What is known of Maya scene files, learned from files Maya writes: fixtures
saved by Maya 2018, 2022, 2024 and 2025 (`samples/models/`), each scene saved
as both `.mb` and `.ma`, and a read-only sweep of 4076 production files (771
`.mb`, 3305 `.ma`). The attribute value layouts were also measured on probe
scenes saved by Maya 2022, 2025 and 2027, one setting per variant, and on
`.mb`/`.ma` twins that Maya 2022 saved from 4 production scenes. "Observed"
marks rules seen in every file so far but not yet probed further.

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
  FOR8 PING            one per extra parent of an instanced node (parent -add)
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
`<refNode>fosterParent1` node and a kind 0 edit. The `.mb` and `.ma` twins
of one scene, saved one after the other, store different UUIDs for that
node. Locking a node is not an edit. All 786 production `.mb` files decode
with these rules and re-encode byte for byte (260k edits).

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

### Attribute flags

The flag byte of an attribute chunk holds the `setAttr` flags of its `.ma`
twin. Measured on 115k chunks of the probes and production twins:

| Bit | Meaning | `.ma` |
|---|---|---|
| `0x01` | Locked | `-l on` |
| `0x04` | Keyable | `-k on`, `-k off` |
| `0x08` | A u32 count comes before the value | `-s <count>` |
| `0x10` | Shown in the channel box | `-cb on` |
| `0x20` | Set on 98% of chunks, meaning not known | |

The keyable bit is the attribute's state, while `.ma` writes `-k` only where
it differs from the attribute's default. `FLGS` chunks hold flags and no
value, like `setAttr -cb on ".rx"`, plus the count when bit `0x08` is set,
like `setAttr -s 3 ".g"`. Bit `0x08` shows up on `FLGS` and empty `CMPD`
chunks only, in 78 files including 25 production files.

- **Only while a flag is off its default.** A flags-only record exists while
  some flag differs from the attribute's default. Unlocking an attribute in
  Maya removes its record.
- **Locks reach children.** Locking a compound locks its children for
  `getAttr -l` and `setAttr`. Locking a child leaves the parent unlocked. The
  file stores the lock on the attribute that was locked only.
- **Not on data records.** On string, matrix and array records the keyable
  and channel box bits are not the attribute's state. Maya 2022 reports a
  keyable string attribute whose `STR ` chunk has the keyable bit clear.
- **`setAttr` refusals.** Maya refuses `setAttr` on a locked or connected
  attribute ("locked or connected and cannot be modified"), and accepts it on
  an attribute an animation curve drives.

### Attribute values

Values are big-endian. `.mb` stores lengths in centimetres, angles in
radians and times in ticks whatever the scene's `currentUnit`, and `.ma`
writes them in the scene's units, times as frames at the scene's rate,
as measured with a scene in metres, degrees and ntsc. Matrices are never
converted, neither in `.ma` nor by `getAttr`. `xform` matrices hold
centimetres and radians in both formats, and a matrix set to translate by
1, 2, 3 in a scene in metres is stored and read as 1, 2, 3.

| Tag | Value |
|---|---|
| `DBLE` | Doubles, also booleans, integers, enums and times |
| `DBL2`, `DBL3`, `DBL4`, `FLT2`, `FLT3` | Doubles or 32-bit floats, as many as the `.ma` statement lists |
| `LNG2`, `LNG3`, `SRT2` | int32 or int16 values |
| `CMPD` | 8 bytes per child value, doubles but for times (int64 ticks), also for float children |
| `STR ` | NUL-terminated text |
| `STR#` | u32 count, then NUL-terminated strings |
| `I32#`, `DBL#` | int32 or double values with no count, the chunk size gives it |
| `PNT#` | 4 doubles per point, no count |
| `VEC#` | 3 doubles per vector, no count |
| `MATR` | 16 doubles, or 328 bytes for an `xform` matrix |

`.ma` writes a range of more than a few hundred elements as several
statements (`.uvst[0].uvsp[0:249]`, then `[250:499]`), whose continuations
leave `-type` out. Doubles are written with `%.17g` in matrices. Elsewhere
some attributes use `%.17g` and others 15 digits when those read back
exactly. Floats take 7 to 10 digits with no rule found. Negative zero is
written `0`.

The time units are written the same in both formats (`game`, `film`,
`pal`, `ntsc`, `show`, `palf`, `ntscf`, `sec`, `millisec`, `min`, `hour`,
`<N>fps`, `29.97df`). Linear and angle units are short in `.mb` headers and
long in `.ma`'s `currentUnit`:

| `.mb` | `mm` | `cm` | `m` | `km` | `in` | `ft` | `yd` | `mi` | `deg` | `rad` |
|---|---|---|---|---|---|---|---|---|---|---|
| `.ma` | `millimeter` | `centimeter` | `meter` | `kilometer` | `inch` | `foot` | `yard` | `mile` | `degree` | `radian` |

A frame lasts 141120000 ticks divided by the rate. Fractional rates are a
whole rate times 1000/1001, so `23.976fps` is 5885880 ticks a frame and
`29.97df` 4708704.

### Attributes across records

One attribute can be set by several records, alike in both formats:

- a value record and a flags record (`.r` and `setAttr -l on ".r"`);
- a parent compound's record (`.t` for `tx`);
- a range holding the element (`pt[1:166]` for `pt[5]`).

They differ in two ways. `.mb` stores a compound's children apart when they
are off their default (`ovcr`, `ovcg`), where `.ma` writes the parent
(`ovrgb`). And `.mb` has size-only `FLGS` chunks and empty `CMPD` chunks
that `.ma` leaves out. When a child of an unstored compound is set, Maya
writes the whole parent (`.t` `5 0 0`).

Attributes the file does not set hold the value a new node of the type starts
with. That is the attribute's default, except on 112 types whose new nodes
start elsewhere: animation curves start with `ihi` 0 and `roti` 1, against
defaults of 2. Default nodes (`select -ne`) are no exception: every value a
new scene gives them off the attribute default is stored. Elements of the
multis a node keeps itself (mesh vertices, animation key tangents) are not
in the file unless set. A multi's stored size can count elements the file
does not list (`pt[0]` at its default before `pt[1:299]`).

`DBLE` stores a time as int64 ticks, 141120000 per second. Read as a double,
a positive tick count is a subnormal number and a negative one is a NaN, so
the bit pattern tells times from other values without the attribute's type.
On 40k `DBLE` values of the probes and twins, this rule found the 129 times
and nothing else. The keys of an `animCurveT*` curve, a `CMPD` chunk named
`ktv`, are pairs of an int64 time and a double, and that double holds ticks
too on an `animCurveTT`.

### Transform matrices (`xform`)

A 328-byte `MATR` value is an `xform` matrix, written `setAttr ".xm[0]"
-type "matrix" "xform" ...` in `.ma`, as on the `xm` attribute of `dagPose`
nodes. Doubles unless noted:

| Offset | Field |
|---|---|
| 0 | Scale |
| 24 | Rotate, in radians |
| 48 | Rotate order as a u32, then 4 bytes not initialised by Maya |
| 56 | Translate |
| 80 | Shear |
| 104 | Scale pivot, negated |
| 128 | Scale pivot translate |
| 152 | Rotate pivot, negated |
| 176 | Rotate pivot translate |
| 200 | Rotate axis, a quaternion of 4 doubles |
| 232 | Joint orient, a quaternion |
| 264 | Inverse scale |
| 288 | A unit quaternion with no `.ma` counterpart |
| 320 | 8 bytes. Bit `0x01` of byte 321 is segment scale compensate, byte 320 is 0 or 1, meaning not known |

`.ma` lists the same fields in the same order, without the extra quaternion,
with the pivots not negated, and ends with `yes` or `no` for segment scale
compensate. All 37 values match on the 1015 `xform` matrices of the fixtures,
probes and production twins. `getAttr` gives the composed 16-value matrix.

### Meshes

A mesh shape stores its geometry in one `MESH` chunk, where `.ma` has `vt`,
`ed`, `fc`, `n`, `uvst` and `clst` statements. In order:

| Part | Layout |
|---|---|
| Points | u32 float count, then x, y, z 32-bit floats per vertex |
| Edges | u32 word count, then 2 vertex indices per edge. Bit 31 of the first one is set on hard edges, whose third `.ma` value is 0 |
| Faces | u32 word count, then a word per face edge. The low 29 bits are the edge index, bit 31 marks an edge used in reverse, written `-1 - index` in `.ma`, bit 30 ends a loop that a hole follows, bit 29 ends a face |
| Normals | u32 float count and the floats, then if there are any, a u32 count and that many words |
| UV sets | u32 count and a u32 of unknown meaning, then per set its name, a u32 float count and the u, v floats, a u32 count and an int32 UV index per face vertex, -1 for none |
| Colour sets | Not decoded yet |

16 bytes or more follow the colour sets, not decoded yet. Points, edges,
faces with holes and UV sets match the `.ma` twins of the probe meshes and
of 4 production scenes.

### Added attributes (`ATTR`)

An `ATTR` chunk is one `addAttr`. Unlike value chunks it has no attribute
name and flag byte in front. It starts with a type code, 4 flag bytes, a
u32 child count and type blocks. Compounds have a u32 count and that many
4-byte value tags, the tag of the attribute's own type first. `aTIM` has two
blocks, every other code one.

| Type code | `addAttr` | Child count | Blocks |
|---|---|---|---|
| `aBOL`, `aBYT`, `aCHR`, `aSI1`, `aLI1`, `aFLT`, `aDBL`, `aAGL`, `aLNR`, `aENM` | `-at` `bool`, `byte`, `char`, `short`, `long`, `float`, `double`, `doubleAngle`, `doubleLinear`, `enum` | 1 | Zero |
| `aTIM` | `-at time` | 2 | `aTIM`, zero |
| `aMSG` | `-at message` | 1 | `aMSG` |
| `aMTX`, `aFMX` | `-at matrix`, `-at fltMatrix` | 1 | `MATR` |
| `aFL2`, `aDB2`, `aLI2`, `aSI2` | `-at float2`, `double2`, `long2`, `short2` | 2 | Count 4, then `FLT2`, `DBL2`, `LNG2`, `SRT2` |
| `aFL3`, `aDB3`, `aLI3`, `aSI3` | `-at float3`, `double3`, `long3`, `short3` | 3 | Count 4, then `FLT3`, `DBL3`, `LNG3`, `SRT3` |
| `aDB4` | `-at double4` | 4 | Count 1, then `DBL4` |
| `aCPD` | `-at compound` | Number of children | Count 1, then `CMPD` |
| `aTYP` | `-dt`. The block is the value tag, `STR `, `STR#`, `MATR`, `DBL#`, `I32#`, `PNT#`, `VEC#`, `MESH`, `NRBC`, `CMP#` or `ATAL` | 1 | The value tag |

Then come `long\0short\0` and, in this order, the fields the flags announce.
The nice name, the enum names of an `aENM` such as `world:local:far=5`, the
parent name, a u32, the minimum and maximum, the soft minimum and maximum,
and `DBLE` with the default value, all numbers as doubles. These numbers are
in internal units in both formats, so `-dv 3` on a `doubleLinear` in a scene
in metres is 3 cm. Maya drops the default of a time attribute.

| Byte | Bit | Meaning |
|---|---|---|
| 0 | | Always 0 |
| 1 | `0x01` | `-usedAsFilename` |
| 1 | `0x02` | `-category` |
| 1 | `0x20` | Storable, clear for `-s false` |
| 2 | `0x02` | Enum names follow |
| 2 | `0x08`, `0x10` | Soft minimum and maximum follow, set together so far |
| 2 | `0x20` | `-hidden` |
| 2 | `0x40` | `-keyable` |
| 2 | `0x80` | A nice name follows |
| 3 | `0x01` | `-multi` |
| 3 | `0x02` | A minimum follows |
| 3 | `0x04` | A maximum follows |
| 3 | `0x08` | A default value follows. `.ma` writes `-dv` only when it is not 0 |
| 3 | `0x20` | A parent name follows, `-parent` |
| 3 | `0x40` | Always set |
| 3 | `0x80` | `-internalSet` |

The names and the `-usedAsFilename` bit match the `addAttr` statements of
the `.ma` twins for all 7268 records of the fixtures, probes and production
twins. The other bits were measured on 6.9k of these records. The names of
all 7.58M records of 849 production `.mb` files decode with these rules.

### Instances

A root `FOR8 PING` group holds one `PRNT` chunk per extra parent (`parent
-add` in `.ma`): a flag byte, the instanced node's absolute path, then the
extra parent (`\x02|hero|heroShape\0heroInstance\0`). An instanced node, and
every node below it, then has one DAG path per parent; connections name each
instance by its full path (`|copies|heroInstance|heroShape.iog`), never by
the short name, even when no other node shares it. No production `.mb` uses
instancing; 2774 `parent -add` statements appear in production `.ma` files.

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

### How nodes are named

Measured by renaming nodes in Maya and saving:

- `CREA` parents, `CWFL` and `RELA` name a node by its short name when no
  other node of the file has it, else by its absolute DAG path, never a
  partial path: with `|root|a|b|leaf` and `|root|c|b|leaf` the file stores
  `|root|a|b|leaf.tx`, though `a|b|leaf` would be unique. (Maya's commands
  show the shortest unique partial path, `x|b|leaf.translateX`; the file does
  not.) After a rename Maya re-derives this for every node involved: a name
  that becomes ambiguous turns into a path, one that becomes unique turns
  back into a short name.
- Reference edits and the `externalContentTable` always name DAG nodes by
  absolute path, other nodes by name.
- Default nodes keep the root namespace prefix (`:initialShadingGroup`).
- `cmds.rename` on a transform also renames its direct shape children whose
  name starts with the old name, by replacing that prefix (`boxShape`,
  `boxShape1`, `boxShapeOrig`, even `boxer`), namespace included
  (`ns:boxShape` > `otherShape`). It leaves `myboxShape`, child transforms
  and deeper nodes alone. Renaming a shape renames nothing else.
- Expressions store direct attribute references compiled (`.O[0] = time *
  2;`, the names live in the connections); names inside MEL commands
  (`getAttr box.ty`) stay as text, and Maya does not update them on rename.
  Neither does it update string attributes holding a node name.

### externalContentTable

The trailer `DATS` declares the columns (`node`, `key`, `upath`, `upathcrc`,
`rpath`, `roles`); `SMTD` is `\0raw\0`, the metadata text, `\0`. The text is
the same in `.ma` (`dataStructure` + `applyMetadata -fmt "raw" -v "..."`):
a channel, a stream, then per row an index line and a values line,
`"node" "fileTextureName" "P:/a.exr" 2550333794 "" "sourceImages"`.

- `node`: absolute DAG path or name; a reference's row names its reference
  node, with an empty key and role `FileRef`.
- `upathcrc`: zlib CRC-32 of the path in the system code page (1252 here),
  even in `.mb`, whose text is UTF-8.
- `rpath`: the resolved path when the file exists, else empty.
- Maya rewrites the rows when a node is renamed or a path changes, and
  accepts a stale table on open.

## Maya ASCII (`.ma`)

A MEL script, in the code page named by its `//Codeset:` comment (1252 for
every production file), with LF or CRLF line endings (CRLF in 1005 of the
3301 non-empty production files, Maya 2016 to 2022). Statements in order:
`file -rdi` / `file -r` per reference, `requires`, `currentUnit`, `fileInfo`,
then `createNode` blocks, `select -ne` blocks for default nodes,
`connectAttr` / `relationship` / `parent -add` (instances), and an optional
`dataStructure` + `applyMetadata` pair, then a `// End of` comment. Some
production files lack the header comments, and some are empty. 3300 of the
3305 production `.ma` files split into `;`-terminated statements and join back
byte for byte; the other 5 hold `.mb` chunks partway through. Split this way,
the first statement carries the header comments in front of it, which a
reader must skip before reading its words (`requires maya "2022"` when the
file has no reference).

- A node is `createNode <type> [-s] -n "name" [-p "parent"]`, followed by its
  own statements: `rename -uid "<UUID>"`, `setAttr`, `addAttr`, `lockNode`.
  The parent is named like in `.mb` (short name, else absolute path).
- `setAttr ".attr" [-type "<type>"] values`: the attribute is the first
  string; strings use `-type "string"`, string arrays `-type "stringArray"`
  with a count, reference edits `-type "dataReferenceEdits"`.
- References: `file -rdi <depth> -ns "ns" [-dr 1] -rfn "RN" [-op "..."] -typ
  "<type>" "path"` at every depth, and `file -r` for the top level. `-typ`
  must match the file: a `.ma` path with `-typ "mayaBinary"` loads nothing,
  silently (the reference still reports loaded). The same holds for the file
  type string of `.mb` `FRDI`/`FREF`.
- `dataReferenceEdits`: the owner, then every group of both sections in one
  list (`"node" count` and the edits), with no section counts. The first
  section ends with the owner's own group (all fixtures and production
  files). Edit arguments are MEL inside a MEL string, so escaped twice. Maya
  2018 to 2022 write the node of an addAttr edit as a bare word
  (`1 |grp|ns:node "note" "note" ...`).
- Long string literals are split into MEL concatenations across lines:
  `"part"` then `+ "part"` on the next line, and inside reference edits
  within parentheses, `("part" + "part")`. A reader must join them; a single
  long literal is just as valid when writing.
- `parent -s -nc -r -add "child" "parent"` (also spelled with long flags,
  `-shape -noConnections -relative -addObject`) adds a parent: instancing.
- `applyMetadata -fmt "raw" -v "<text>"` holds the externalContentTable text.
- Maya writes short flags, but 8 production files, written by scripts, use
  long ones throughout (`createNode transform -name "ALL" -parent "rig"`,
  `rename -uuid`, `select -noExpand`, `connectAttr -nextAvailable`). A reader
  must accept both spellings of every flag it reads. No production file puts
  `createNode` flags before the type or spells `setAttr -type` short.
- Opening a `.ma`, Maya keeps its own node (and UUID) for the nodes every
  scene starts with (`lightLinker1`, `layerManager`, `renderLayerManager`),
  though the file stores a UUID for them.

## Shared traps

- Text encoding: `.mb` text is UTF-8 (`STR ` values, metadata); `.ma` text
  uses the code page of its `//Codeset:` comment. The `externalContentTable`
  CRC is computed on the code page bytes in both.
- `.mb` stores lengths in centimetres and angles in radians, `.ma` in the
  scene's `currentUnit`, so moving a value between the formats needs the
  scene's units.
- `externalContentTable` lists some file dependencies, but only in about 69%
  of production `.mb` and 21% of `.ma`, and never Alembic or Arnold nodes.
- Maya-native path attributes use forward slashes; plugin attributes store
  what they were given (production Yeti `cfn` values use backslashes).
