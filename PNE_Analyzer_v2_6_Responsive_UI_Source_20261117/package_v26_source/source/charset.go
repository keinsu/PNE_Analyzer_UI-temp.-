package main

import (
	_ "embed"
	"encoding/binary"
	"strings"
)

//go:embed cp949_table.bin
var cp949Table []byte

// decodeCP949 converts Windows Korean CP949 without requiring external modules
// or an OS codec. The mapping table is generated from the Python CP949 codec.
func decodeCP949(b []byte) string {
	var sb strings.Builder
	sb.Grow(len(b))
	for i := 0; i < len(b); i++ {
		ch := b[i]
		if ch < 0x80 {
			sb.WriteByte(ch)
			continue
		}
		if ch >= 0x81 && i+1 < len(b) {
			next := b[i+1]
			pos := ((int(ch)-0x81)*256 + int(next)) * 2
			if pos+1 < len(cp949Table) {
				cp := binary.LittleEndian.Uint16(cp949Table[pos : pos+2])
				if cp != 0 {
					sb.WriteRune(rune(cp))
					i++
					continue
				}
			}
		}
		sb.WriteRune('�')
	}
	return sb.String()
}
