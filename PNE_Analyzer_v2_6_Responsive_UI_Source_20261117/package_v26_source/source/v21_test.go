package main

import (
	"math"
	"strings"
	"testing"
)

func TestBaselineCycleMatches100(t *testing.T) {
	a := analyzed{pair: pair{id: "CH001"}}
	a.csvRows = []stepRow{
		{num: "3", typ: "방전", curCycle: "1", capacity: 3, hasCap: true},
		{num: "3", typ: "방전", curCycle: "5", capacity: 2.7, hasCap: true},
	}
	a.segments = []segment{{row: 0, status: "MATCHED"}, {row: 1, status: "MATCHED"}}
	got := lifeFull([]analyzed{a}, []int{0}, "5")
	if len(got) != 1 || len(got[0].Points) != 2 {
		t.Fatalf("unexpected %+v", got)
	}
	if math.Abs(got[0].Points[1].Retention-100) > 1e-8 || math.Abs(got[0].Points[0].Retention-(3.0/2.7*100)) > 1e-8 {
		t.Fatalf("wrong baseline %+v", got[0].Points)
	}
	if err := validateLifeBase([]analyzed{a}, []int{0}, "10"); err == nil || !strings.Contains(err.Error(), "없습니다") {
		t.Fatal("missing reference must error", err)
	}
}
func TestV21ChartModeFilters(t *testing.T) {
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
	for _, s := range a.segments {
		a.records += s.count
	}
	matchSteps(&a)
	all, err := chartPoints(a, "1", "", "voltage", "all")
	if err != nil {
		t.Fatal(err)
	}
	dis, err := chartPoints(a, "1", "", "voltage", "discharge")
	if err != nil {
		t.Fatal(err)
	}
	if len(dis) == 0 || len(dis) >= len(all) {
		t.Fatalf("bad mode filtering all=%d dis=%d", len(all), len(dis))
	}
	for _, p := range all {
		if p[0] < 0 || p[0] > 1e10 {
			t.Fatalf("bad time axis %v", p)
		}
	}
}
