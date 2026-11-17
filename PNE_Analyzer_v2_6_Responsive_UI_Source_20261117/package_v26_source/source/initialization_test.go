package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"math"
	"os"
	"path/filepath"
	"testing"
)

func TestInitializationAndBatch(t *testing.T) {
	inp, err := os.ReadFile("synthetic_test_nested.zip")
	if err != nil {
		t.Fatal(err)
	}
	zr, err := zip.NewReader(bytes.NewReader(inp), int64(len(inp)))
	if err != nil {
		t.Fatal(err)
	}
	newZip := filepath.Join(t.TempDir(), "init.zip")
	f, err := os.Create(newZip)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(f)
	for _, entry := range zr.File {
		if entry.Name == "../unsafe.cyc" {
			continue
		}
		r, err := entry.Open()
		if err != nil {
			t.Fatal(err)
		}
		buf := new(bytes.Buffer)
		buf.ReadFrom(r)
		r.Close()
		data := buf.Bytes()
		if filepath.Ext(entry.Name) == ".cyc" {
			rec := make([]byte, 176)
			binary.LittleEndian.PutUint32(rec[4:], math.Float32bits(7.04e28))
			binary.LittleEndian.PutUint32(rec[16:], math.Float32bits(6.98e19))
			data = append(append(append([]byte{}, data[:72]...), rec...), data[72:]...)
		}
		w, err := zw.Create(entry.Name)
		if err != nil {
			t.Fatal(err)
		}
		w.Write(data)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	out := filepath.Join(t.TempDir(), "out")
	all, err := runDetailed(newZip, out, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 3 {
		t.Fatalf("channels: %d", len(all))
	}
	for _, a := range all {
		if a.initialized != 1 || a.matched != 4 || a.unmatched != 0 {
			t.Fatalf("%s got init %d matched %d unmatched %d", a.pair.name, a.initialized, a.matched, a.unmatched)
		}
		if a.segments[0].first != 1 || a.segments[0].status != "INITIALIZATION" {
			t.Fatal("initial not flagged")
		}
	}
}
func TestChartFromZip(t *testing.T) {
	ps, cleanup, err := findPairs("synthetic_test_nested.zip")
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	a := analyzed{pair: ps[0]}
	a.csvRows, err = readStepCSV(ps[0].csv)
	if err != nil {
		t.Fatal(err)
	}
	a.segments, err = readSegments(ps[0].cyc)
	if err != nil {
		t.Fatal(err)
	}
	a.records = 0
	for _, s := range a.segments {
		a.records += s.count
	}
	matchSteps(&a)
	points, err := chartPoints(a, "1", "3", "voltage")
	if err != nil {
		t.Fatal(err)
	}
	if len(points) != 5 {
		t.Fatalf("expected 5 points got %d", len(points))
	}
	if math.Abs(points[len(points)-1][1]-2.5) > 1e-6 {
		t.Fatalf("unexpected last voltage: %v", points)
	}
}
