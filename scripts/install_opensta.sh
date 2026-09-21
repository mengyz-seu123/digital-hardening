#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/tools/opensta" "$ROOT/tools/bin"
cd "$ROOT/tools/opensta"
ar x ../../downloads/opensta_0~20191111gitc018cb2+dfsg-1build1_amd64.deb
env -u LD_LIBRARY_PATH tar -xf data.tar.xz
cat > "$ROOT/tools/bin/sta" <<'EOF'
#!/usr/bin/env bash
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export TCL_LIBRARY="$ROOT/tools/oss-cad-suite/lib/tcl8.6"
exec "$ROOT/tools/oss-cad-suite/lib/ld-linux-x86-64.so.2" --inhibit-cache --inhibit-rpath "" --library-path "$ROOT/tools/oss-cad-suite/lib" "$ROOT/tools/opensta/usr/bin/sta" "$@"
EOF
chmod +x "$ROOT/tools/bin/sta"
"$ROOT/tools/bin/sta" -version
