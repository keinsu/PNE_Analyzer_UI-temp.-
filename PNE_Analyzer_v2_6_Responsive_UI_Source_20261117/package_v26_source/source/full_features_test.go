package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"io"
	"math"
	"path/filepath"
	"strings"
	"testing"
)

func TestRangeAndStepSelection(t *testing.T) {
	s, e := cycleSet("1,5,10-20:5", []string{"1", "5", "10", "15", "20"})
	if e != nil || len(s) != 5 || !s["15"] {
		t.Fatal(s, e)
	}
	step, e := parseStepIDs("2,10,20-22")
	if e != nil || !step["2"] || !step["21"] || len(step) != 5 {
		t.Fatal(step, e)
	}
}
func TestAdvancedDQLinearAndDischargeNegative(t *testing.T) {
	pts := make([][2]float64, 130)
	for i := range pts {
		pts[i] = [2]float64{4.2 - float64(i)*.006, float64(i) * .025}
	}
	cfg := dqSettings{Points: 500, Smooth: true, Window: 0, Order: 3, Sensitivity: .05}
	r, _, e := computeDQAdvanced(pts, "discharge", cfg)
	if e != nil {
		t.Fatal(e)
	}
	for i := 10; i < len(r)-10; i++ {
		if !(r[i].Smooth < 0) {
			t.Fatalf("discharge sign %v", r[i])
		}
	}
	cfg.Smooth = false
	r2, _, e := computeDQAdvanced(pts, "discharge", cfg)
	if e != nil || len(r2) != 500 {
		t.Fatalf("smoothing toggle %v", e)
	}
}
func buildTestRecord(stepTime, v, i, cap float32) []byte {
	b := make([]byte, recordBytes)
	for off, value := range map[int]float32{4: stepTime, 16: v, 20: i, 24: cap, 72: v * i / 1000} {
		binary.LittleEndian.PutUint32(b[off:], math.Float32bits(value))
	}
	return b
}
func TestDCRPulseReturnAnd10s(t *testing.T) {
	type stepDef struct {
		cycle, step, typ string
		times            []float32
		v, i, cap        float32
	}
	defs := []stepDef{
		{"1", "1", "Rest", []float32{0, 60}, 4100, 0, 0},
		{"1", "2", "방전", []float32{0, 1, 10, 30}, 4100, -10000, 100},
		{"1", "3", "충전", []float32{0, 60}, 4000, 6000, 100},
		{"1", "4", "충전", []float32{0, 1, 10, 30}, 4000, 10000, 100},
		{"1", "5", "방전", []float32{0, 60}, 4100, -6000, 100},
	}
	raw := make([]byte, headerBytes)
	a := analyzed{csvRows: []stepRow{}, segments: []segment{}}
	n := int64(1)
	for _, d := range defs {
		first := n
		var firstR, lastR rec
		for j, tm := range d.times {
			v := d.v
			if d.step == "2" && tm == 10 {
				v = 4000
			}
			if d.step == "2" && tm == 30 {
				v = 3980
			}
			raw = append(raw, buildTestRecord(tm, v, d.i, d.cap)...)
			r := decodeRec(raw[len(raw)-recordBytes:])
			if j == 0 {
				firstR = r
			}
			lastR = r
			n++
		}
		a.csvRows = append(a.csvRows, stepRow{num: d.step, typ: d.typ, curCycle: d.cycle})
		a.segments = append(a.segments, segment{first: first, last: n - 1, count: n - first, firstRec: firstR, lastRec: lastR, row: len(a.csvRows) - 1, status: "MATCHED"})
	}
	a.records = n - 1
	a.pair.id = "CH01"
	a.pair.cyc = sourceFile{open: func() (io.ReadCloser, error) { return io.NopCloser(bytes.NewReader(raw)), nil }}
	v, e := computeDCR([]analyzed{a}, dcrConfig{Channels: []int{0}, Times: []float64{1, 10, 30}, MinCurrentA: 1, MaxPulseSec: 40})
	if e != nil {
		t.Fatal(e)
	}
	if len(v.Pulses) != 2 {
		t.Fatalf("expected discharge/charge pulses, got %+v", v.Pulses)
	}
	p := v.Pulses[0]
	if p.Direction != "방전" || p.ReturnStep != "3" || p.V0 != 4.1 {
		t.Fatalf("first pulse %+v", p)
	}
	if math.Abs(p.Values[1].Resistance-10) > .001 {
		t.Fatalf("R(10s) %+v", p.Values)
	}
	if !p.Values[1].Available {
		t.Fatal("missing available")
	}
}
func TestXLSXBasic(t *testing.T) {
	dest := filepath.Join(t.TempDir(), "dcr.xlsx")
	if e := writeXLSX(dest, []xlsxSheet{{Name: "DCIR 10s", Rows: [][]string{{"Cycle", "DCIR"}, {"001", "10.0"}}}}); e != nil {
		t.Fatal(e)
	}
	z, e := zip.OpenReader(dest)
	if e != nil {
		t.Fatal(e)
	}
	defer z.Close()
	found := false
	for _, f := range z.File {
		if strings.Contains(f.Name, "sheet1.xml") {
			found = true
		}
	}
	if !found {
		t.Fatal("missing sheet1.xml")
	}
}
