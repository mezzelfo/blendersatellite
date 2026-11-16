#!/bin/bash
# Download the study archives (one ZIP per tile, 6 in parallel) into $1/archives.
set -euo pipefail
STUDY="$1"; shift
URL='https://gis-3dmodelcapture-2020.s3.ap-southeast-2.amazonaws.com/photomesh/OBJ/%s.zip'
printf '%s\n' "$@" | xargs -P 6 -I{} sh -c "curl -sS --fail --retry 3 -o '$STUDY/archives/{}.zip.part' \"\$(printf '$URL' {})\" && mv '$STUDY/archives/{}.zip.part' '$STUDY/archives/{}.zip' && echo done {}"
