package main

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestV22EnergyBaselineAndRepresentativeStep(t *testing.T) {
	a := analyzed{pair: pair{id: "CH13"}}
	a.csvRows = []stepRow{
		{num: "2", typ: "방전", curCycle: "1", capacity: 3, energy: 11, hasCap: true},
		{num: "3", typ: "방전", curCycle: "1", capacity: .2, energy: 30, hasCap: true},
		{num: "4", typ: "충전", curCycle: "1", capacity: 3.1, energy: 13, hasCap: true},
		{num: "2", typ: "방전", curCycle: "2", capacity: 2.7, energy: 9.9, hasCap: true},
		{num: "4", typ: "충전", curCycle: "2", capacity: 2.8, energy: 12, hasCap: true},
	}
	for j := range a.csvRows {
		a.segments = append(a.segments, segment{row: j, status: "MATCHED", lastRec: rec{temp: 40}})
	}
	got := lifeFull([]analyzed{a}, []int{0}, "2")
	if len(got) != 1 || len(got[0].Points) != 2 {
		t.Fatalf("unexpected result %+v", got)
	}
	a1, a2 := got[0].Points[0], got[0].Points[1]
	if math.Abs(a1.EnergyRetention-100*11/9.9) > 1e-7 || math.Abs(a2.EnergyRetention-100) > 1e-7 {
		t.Fatalf("energy retention %+v %+v", a1, a2)
	}
	if a1.DischargeEnergy != 41 || a1.DischargeEnergyRep != 11 {
		t.Fatalf("energy should follow representative capacity step: %+v", a1)
	}
	if a1.Retention <= 100 || a2.Retention != 100 {
		t.Fatalf("reference wrong %+v %+v", a1, a2)
	}
	if err := validateLifeBase([]analyzed{a}, []int{0}, "3"); err == nil {
		t.Fatal("unknown reference allowed")
	}
}
func TestV22RawXAxisAndOptInRaw(t *testing.T) {
	ps, cleanup, err := findPairs("synthetic_test_nested.zip")
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		t.Fatal(err)
	}
	a := analyzed{pair: ps[0]}
	a.csvRows, err = readStepCSV(ps[0].csv)
	if err != nil {
		t.Fatal(err)
	}
	a.segments, err = readSegments(ps[0].cyc)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range a.segments {
		a.records += s.count
	}
	matchSteps(&a)
	tm, err := chartPointsAxis(a, "1", "", "voltage", "all", "time")
	if err != nil || len(tm) == 0 {
		t.Fatalf("time chart: %v", err)
	}
	q, err := chartPointsAxis(a, "1", "", "voltage", "all", "capacity")
	if err != nil || len(q) != len(tm) {
		t.Fatalf("capacity chart %v", err)
	}
	v, err := chartPointsAxis(a, "1", "", "voltage", "all", "voltage")
	if err != nil || len(v) != len(tm) {
		t.Fatalf("voltage chart %v", err)
	}
	if math.Abs(v[2][0]-v[2][1]) > 1e-10 {
		t.Fatalf("voltage axis values not identical %v", v[2])
	}
	if _, err := chartPointsAxis(a, "1", "", "voltage", "all", "invalid"); err == nil {
		t.Fatal("unsupported axis should fail")
	}
	out := t.TempDir()
	_, err = runDetailed("synthetic_test_nested.zip", out, false)
	if err != nil {
		t.Fatal(err)
	}
	raws, _ := filepath.Glob(filepath.Join(out, "*_Raw_Data.csv"))
	if len(raws) != 0 {
		t.Fatalf("raw generated without opt-in: %v", raws)
	}
	steps, _ := filepath.Glob(filepath.Join(out, "*_Step_Matching.csv"))
	if len(steps) != 3 {
		t.Fatalf("step reports missing: %v", steps)
	}
	_, err = os.Stat(filepath.Join(out, "PNE_Analysis_Report.html"))
	if err != nil {
		t.Fatal(err)
	}
}
func TestV22GUIContainsAllControls(t *testing.T) {
	for _, needle := range []string{"id=\"rawX\"", "id=\"summaryRetentionPlot\"", "id=\"summaryEnergyPlot\"", "id=\"exportRawSelected\"", "id=\"toggleImport\""} {
		if !strings.Contains(guiHTML+fullHTML, needle) {
			t.Fatalf("missing UI element %s", needle)
		}
	}
	if strings.Contains(guiHTML, `id="raw" checked`) {
		t.Fatal("Raw CSV default should not be checked")
	}
	if strings.Contains(fullJS, `$('fullLifeMetric').value='retention'`) {
		t.Fatal("life metric should remain selected when baseline changes")
	}
}
