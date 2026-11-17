package main

import (
	"encoding/binary"
	"io"
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestSavitzkyGolayLinearPreservation(t *testing.T) {
	y := make([]float64, 100)
	for i := range y {
		y[i] = 3.3 + 1.2*float64(i)
	}
	out := sgFilter(y, 21, 3)
	for i := range y {
		if math.Abs(out[i]-y[i]) > 1e-7 {
			t.Fatalf("SG mismatch at %d: %g != %g", i, out[i], y[i])
		}
	}
}
func TestDQDVLinearCapacity(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "sample.cyc")
	f, e := os.Create(path)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = f.Write(make([]byte, headerBytes)); e != nil {
		t.Fatal(e)
	}
	const N = 121
	for i := 0; i < N; i++ {
		b := make([]byte, recordBytes)
		voltage := 3 + float64(i)*0.01
		capacity := 2 * (voltage - 3)
		binary.LittleEndian.PutUint32(b[16:], math.Float32bits(float32(voltage*1000)))
		binary.LittleEndian.PutUint32(b[24:], math.Float32bits(float32(capacity*1000)))
		if _, e = f.Write(b); e != nil {
			t.Fatal(e)
		}
	}
	f.Close()
	a := analyzed{pair: pair{cyc: sourceFile{open: func() (io.ReadCloser, error) { return os.Open(path) }}}, segments: []segment{{first: 1, last: N, count: N, row: 0, status: "MATCHED"}}, csvRows: []stepRow{{typ: "충전", curCycle: "1", num: "7"}}}
	result, e := dqdvSeries(a, "1", "7", "charge", 0.001, 11)
	if e != nil {
		t.Fatal(e)
	}
	if len(result) < 1000 {
		t.Fatalf("points %d", len(result))
	}
	for i := 50; i < len(result)-50; i += 17 {
		if math.Abs(result[i].Smooth-2) > 0.02 {
			t.Fatalf("unexpected dQdV %g", result[i].Smooth)
		}
	}
}
func TestLifeRepresentativeSelection(t *testing.T) {
	a := analyzed{csvRows: []stepRow{{num: "7", typ: "충전", curCycle: "1", capacity: 10, hasCap: true}, {num: "9", typ: "방전", curCycle: "1", capacity: 9, hasCap: true}, {num: "7", typ: "충전", curCycle: "2", capacity: 10, hasCap: true}, {num: "9", typ: "방전", curCycle: "2", capacity: 8.5, hasCap: true}}, segments: []segment{{row: 0, status: "MATCHED"}, {row: 1, status: "MATCHED"}, {row: 2, status: "MATCHED"}, {row: 3, status: "MATCHED"}}}
	pts := lifeSeries(a)
	if len(pts) != 2 || math.Abs(pts[1].Retention-100*8.5/9) > 1e-7 || math.Abs(pts[0].CE-90) > 1e-7 {
		t.Fatalf("life %+v", pts)
	}
}
