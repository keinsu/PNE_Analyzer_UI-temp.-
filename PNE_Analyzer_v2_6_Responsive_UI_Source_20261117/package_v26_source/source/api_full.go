package main

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
)

func registerFullAPI(mux *http.ServeMux, u *uiState, safe func(http.ResponseWriter, *http.Request) bool) {
	snapshot := func() ([]analyzed, string) {
		u.Lock()
		defer u.Unlock()
		return append([]analyzed(nil), u.all...), u.output
	}
	parse := func(w http.ResponseWriter, r *http.Request, v any) bool {
		if !safe(w, r) {
			return false
		}
		if r.Method != "POST" {
			http.Error(w, "POST required", 405)
			return false
		}
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<20)).Decode(v); e != nil {
			http.Error(w, "JSON 형식 오류: "+e.Error(), 400)
			return false
		}
		return true
	}
	// Raw export is opt-in. The initial ZIP analysis never writes per-channel Raw CSV by default.
	mux.HandleFunc("/api/exportRaw", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Channel int `json:"channel"`
		}
		if !parse(w, r, &q) {
			return
		}
		all, out := snapshot()
		if q.Channel < 0 || q.Channel >= len(all) || out == "" {
			http.Error(w, "분석된 채널을 선택하세요", 422)
			return
		}
		a := all[q.Channel]
		if a.records <= 0 || len(a.segments) == 0 {
			http.Error(w, "Raw 데이터가 없습니다", 422)
			return
		}
		target := filepath.Join(out, a.id+"_Raw_Data.csv")
		if err := exportRaw(target, &a); err != nil {
			_ = os.Remove(target)
			http.Error(w, "Raw CSV 생성 실패: "+err.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": target, "records": a.records})
	})
	mux.HandleFunc("/api/lifeFull", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Channels []int  `json:"channels"`
			Base     string `json:"base"`
		}
		if !parse(w, r, &q) {
			return
		}
		all, _ := snapshot()
		if len(all) == 0 {
			http.Error(w, "먼저 ZIP을 분석하세요", 422)
			return
		}
		if e := validateLifeBase(all, q.Channels, q.Base); e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		sendJSON(w, map[string]any{"series": lifeFull(all, q.Channels, q.Base), "note": "대표 용량은 Cycle에서 가장 큰 Step 용량입니다. 복수 Step의 합계도 함께 제공하며, 펄스 시험의 단순 합계는 전체 용량을 대표하지 않을 수 있습니다."})
	})
	mux.HandleFunc("/api/lifeFullExport", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Channels []int  `json:"channels"`
			Base     string `json:"base"`
		}
		if !parse(w, r, &q) {
			return
		}
		all, out := snapshot()
		if len(all) == 0 || out == "" {
			http.Error(w, "분석 결과 없음", 422)
			return
		}
		if e := validateLifeBase(all, q.Channels, q.Base); e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		target := filepath.Join(out, "PNE_Cycle_Life_Selected.csv")
		if e := exportLifeFull(target, lifeFull(all, q.Channels, q.Base)); e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": target})
	})
	mux.HandleFunc("/api/dqFull", func(w http.ResponseWriter, r *http.Request) {
		var q dqSettings
		if !parse(w, r, &q) {
			return
		}
		all, _ := snapshot()
		if len(all) == 0 {
			http.Error(w, "먼저 ZIP을 분석하세요", 422)
			return
		}
		cs, e := dqAdvancedCurves(all, q)
		if e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		total := 0
		for _, c := range cs {
			total += len(c.Points)
		}
		if total > 600000 {
			http.Error(w, "그래프 최대 600,000포인트입니다. Cycle/채널을 줄여주세요", 422)
			return
		}
		sendJSON(w, map[string]any{"curves": cs, "count": len(cs), "note": "전압 정렬·5µV 중복 평균·CC 단조 구간 보간·SG(선택)·미분·피크 검출. 실제 CV 경계는 시험 스케줄과 대조하세요."})
	})
	mux.HandleFunc("/api/dqFullExport", func(w http.ResponseWriter, r *http.Request) {
		var q dqSettings
		if !parse(w, r, &q) {
			return
		}
		all, out := snapshot()
		if len(all) == 0 || out == "" {
			http.Error(w, "분석 결과 없음", 422)
			return
		}
		cs, e := dqAdvancedCurves(all, q)
		if e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		target := filepath.Join(out, "PNE_dQdV_Overlay_Selected.csv")
		if e = saveDQAdvanced(target, cs); e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		peaksFile := filepath.Join(out, "PNE_dQdV_Peaks_Selected.csv")
		rows := [][]string{{"Source_File", "Cycle", "Direction", "StepNo", "PeakVoltage_V", "PeakValue_Ah_per_V"}}
		for _, c := range cs {
			for _, p := range c.Peaks {
				rows = append(rows, []string{safeCell(c.Name), safeCell(c.Cycle), c.Direction, safeCell(c.Step), ff(p.Voltage), ff(p.Value)})
			}
		}
		if e = writeRows(peaksFile, rows); e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": target, "peaks": peaksFile})
	})
	mux.HandleFunc("/api/dcrFull", func(w http.ResponseWriter, r *http.Request) {
		var q dcrConfig
		if !parse(w, r, &q) {
			return
		}
		all, _ := snapshot()
		if len(all) == 0 {
			http.Error(w, "먼저 ZIP을 분석하세요", 422)
			return
		}
		v, e := computeDCR(all, q)
		if e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		sendJSON(w, v)
	})
	mux.HandleFunc("/api/dcrFullExport", func(w http.ResponseWriter, r *http.Request) {
		var q struct {
			Items  []dcrPulse `json:"items"`
			Times  []float64  `json:"times"`
			Format string     `json:"format"`
			Time   float64    `json:"time"`
		}
		if !parse(w, r, &q) {
			return
		}
		_, out := snapshot()
		if out == "" {
			http.Error(w, "먼저 ZIP을 분석하세요", 422)
			return
		}
		if len(q.Items) > 10000 {
			http.Error(w, "펄스 최대 10000개", 422)
			return
		}
		for _, p := range q.Items {
			if len(p.Values) > 12 {
				http.Error(w, "DCR 시점 초과", 422)
				return
			}
		}
		var target string
		var e error
		switch q.Format {
		case "raw":
			target = filepath.Join(out, "PNE_DCR_Raw_Selected.csv")
			e = exportDCRRawCSV(target, q.Items)
		case "summary":
			target = filepath.Join(out, "PNE_DCR_Summary_"+strconv.FormatFloat(q.Time, 'f', 2, 64)+"s.csv")
			e = writeRows(target, dcrSummaryRows(q.Items, q.Time))
		case "excel":
			target = filepath.Join(out, "PNE_DCR_All_Selected.xlsx")
			sheets := []xlsxSheet{}
			for _, t := range q.Times {
				sheets = append(sheets, xlsxSheet{Name: fmt.Sprintf("DCIR %.2fs", t), Rows: dcrSummaryRows(q.Items, t)})
			}
			sheets = append(sheets, xlsxSheet{Name: "Raw Results", Rows: dcrRawRows(q.Items)})
			e = writeXLSX(target, sheets)
		default:
			http.Error(w, "저장 형식 오류", 400)
			return
		}
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": target})
	})
}
func writeRows(path string, rows [][]string) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	f.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(f)
	for _, r := range rows {
		if e = w.Write(r); e != nil {
			return e
		}
	}
	w.Flush()
	return w.Error()
}
