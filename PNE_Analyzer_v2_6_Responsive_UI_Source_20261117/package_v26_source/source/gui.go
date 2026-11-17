package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/binary"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf16"
)

type uiState struct {
	sync.Mutex
	token         string
	busy          bool
	err           string
	all           []analyzed
	output        string
	uploadedInput string
	progress      string
}

func (s *uiState) setProgress(msg string) { s.Lock(); s.progress = msg; s.Unlock() }
func guiMain(args []string) {
	b := make([]byte, 24)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	u := &uiState{token: base64.RawURLEncoding.EncodeToString(b)}
	initial := ""
	if len(args) > 0 {
		initial = strings.Trim(args[0], "\"' ")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'")
		html := strings.ReplaceAll(guiHTML, "</style>", fullCSS+"</style>")
		html = strings.ReplaceAll(html, `<section class="panel" id="analysis"`, fullNav+`<section class="panel" id="analysis"`)
		html = strings.ReplaceAll(html, "</main>", fullHTML+"</main>")
		html = strings.ReplaceAll(html, "</script>", fullJS+plotEnhanceJS+"</script>")
		html = strings.ReplaceAll(html, "__TOKEN__", u.token)
		html = strings.ReplaceAll(html, "__INITIAL__", strconv.Quote(initial))
		io.WriteString(w, html)
	})
	safe := func(w http.ResponseWriter, r *http.Request) bool {
		if r.Header.Get("X-PNE-Token") != u.token {
			http.Error(w, "unauthorized", 403)
			return false
		}
		if r.Method != "POST" && r.Method != "GET" {
			http.Error(w, "method not allowed", 405)
			return false
		}
		return true
	}
	mux.HandleFunc("/api/pick", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		if r.Method != "POST" {
			http.Error(w, "method", 405)
			return
		}
		t := r.URL.Query().Get("type")
		value, e := nativePick(t)
		if e != nil {
			sendJSON(w, map[string]any{"error": e.Error()})
			return
		}
		sendJSON(w, map[string]any{"path": value, "suggestedOutput": defaultOutputFor(value)})
	})
	// Browser drop is sandboxed: it does not reveal the original absolute path.
	// Accept the file locally and return a temporary local input path.
	mux.HandleFunc("/api/drop", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) || r.Method != "POST" {
			return
		}
		filename, nameErr := url.QueryUnescape(r.Header.Get("X-PNE-Filename"))
		if nameErr != nil || filename == "" || !strings.EqualFold(filepath.Ext(filename), ".zip") {
			http.Error(w, "ZIP 파일만 지원합니다", 400)
			return
		}
		const maxUpload int64 = 2 << 30
		f, e := os.CreateTemp("", "pne_drop_*.zip")
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		tempName := f.Name()
		n, e := io.Copy(f, io.LimitReader(r.Body, maxUpload+1))
		ce := f.Close()
		if e != nil || ce != nil || n > maxUpload {
			os.Remove(tempName)
			http.Error(w, "ZIP 복사 실패 또는 2GB 초과", 400)
			return
		}
		u.Lock()
		old := u.uploadedInput
		u.uploadedInput = tempName
		u.Unlock()
		if old != "" {
			os.Remove(old)
		}
		sendJSON(w, map[string]any{"path": tempName, "suggestedOutput": defaultOutputForDrop(), "note": "브라우저 드롭은 원본 폴더 위치를 읽을 수 없습니다. 결과 저장 폴더를 확인하세요."})
	})
	mux.HandleFunc("/api/start", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		if r.Method != "POST" {
			http.Error(w, "method", 405)
			return
		}
		var q struct {
			Input  string `json:"input"`
			Output string `json:"output"`
			Raw    bool   `json:"raw"`
		}
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 10000)).Decode(&q); e != nil {
			http.Error(w, "bad request", 400)
			return
		}
		q.Input = strings.TrimSpace(q.Input)
		q.Output = strings.TrimSpace(q.Output)
		if q.Input == "" {
			http.Error(w, "ZIP 또는 데이터 폴더 경로가 필요합니다", 400)
			return
		}
		if q.Output == "" {
			q.Output = defaultOutputFor(q.Input)
		}
		u.Lock()
		if u.busy {
			u.Unlock()
			http.Error(w, "already running", 409)
			return
		}
		u.busy = true
		u.progress = "분석 시작"
		u.err = ""
		u.all = nil
		u.Unlock()
		go func() {
			u.setProgress("채널 탐색 및 CYC/CSV 분석 중 · 파일 크기에 따라 시간이 걸릴 수 있습니다")
			a, e := runDetailed(q.Input, q.Output, q.Raw)
			u.Lock()
			u.all = a
			u.output = q.Output
			u.busy = false
			if e != nil {
				u.err = e.Error()
				u.progress = "분석 실패"
			} else {
				u.progress = "분석 완료"
			}
			u.Unlock()
		}()
		sendJSON(w, map[string]any{"started": true})
	})
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		u.Lock()
		defer u.Unlock()
		items := []map[string]any{}
		for i, a := range u.all {
			cycles := map[string]bool{}
			steps := map[string]bool{}
			for _, s := range a.segments {
				if s.status != "MATCHED" && s.status != "REVIEW" {
					continue
				}
				if s.row >= 0 {
					rr := a.csvRows[s.row]
					cycles[rr.curCycle] = true
					steps[rr.num] = true
				}
			}
			cs := []string{}
			ss := []string{}
			for c := range cycles {
				if c != "" {
					cs = append(cs, c)
				}
			}
			for st := range steps {
				if st != "" {
					ss = append(ss, st)
				}
			}
			items = append(items, map[string]any{"id": i, "name": a.pair.id, "records": a.records, "matched": a.matched, "review": a.review, "unmatched": a.unmatched, "initialized": a.initialized, "cycles": cs, "steps": ss})
		}
		sendJSON(w, map[string]any{"busy": u.busy, "error": u.err, "progress": u.progress, "channels": items, "output": u.output})
	})
	mux.HandleFunc("/api/chart", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		ch, e := strconv.Atoi(r.URL.Query().Get("channel"))
		if e != nil {
			http.Error(w, "invalid channel", 400)
			return
		}
		cycle := r.URL.Query().Get("cycle")
		step := r.URL.Query().Get("step")
		metric := r.URL.Query().Get("metric")
		mode := r.URL.Query().Get("mode")
		u.Lock()
		if ch < 0 || ch >= len(u.all) {
			u.Unlock()
			http.Error(w, "channel not found", 404)
			return
		}
		a := u.all[ch]
		u.Unlock()
		points, e := chartPointsAxis(a, cycle, step, metric, mode, r.URL.Query().Get("x"))
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"points": points, "count": len(points), "metric": metric})
	})
	mux.HandleFunc("/api/life", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		u.Lock()
		all := append([]analyzed(nil), u.all...)
		u.Unlock()
		series := []map[string]any{}
		for i, a := range all {
			series = append(series, map[string]any{"channel": i, "name": a.pair.id, "points": lifeSeries(a)})
		}
		sendJSON(w, map[string]any{"series": series, "note": "StepEnd의 각 Cycle에서 가장 큰 충/방전 Step 용량을 대표값으로 선택한 예비 분석입니다. 여러 구간으로 나뉜 실제 용량·CE·기준 Cycle을 검증하세요."})
	})
	mux.HandleFunc("/api/exportLife", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) || r.Method != "POST" {
			return
		}
		u.Lock()
		all := append([]analyzed(nil), u.all...)
		out := u.output
		u.Unlock()
		if len(all) == 0 {
			http.Error(w, "분석 결과가 없습니다", 422)
			return
		}
		target := filepath.Join(out, "PNE_Cycle_Life_All_Channels.csv")
		f, e := os.Create(target)
		if e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		writer := csv.NewWriter(f)
		writer.Write([]string{"Channel", "Cycle", "Charge_Ah_Representative", "Discharge_Ah_Representative", "CE_Percent", "Retention_Percent_FirstValidDischarge", "10s_Impedance_mOhm_Candidate", "HasCharge", "HasDischarge"})
		for _, a := range all {
			for _, p := range lifeSeries(a) {
				writer.Write([]string{safeCell(a.pair.id), safeCell(p.Cycle), ff(p.Charge), ff(p.Discharge), ff(p.CE), ff(p.Retention), ff(p.Imp10), strconv.FormatBool(p.HasCharge), strconv.FormatBool(p.HasDischarge)})
			}
		}
		writer.Flush()
		err := writer.Error()
		if closeErr := f.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": target})
	})
	mux.HandleFunc("/api/dqdv", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		ch, e := strconv.Atoi(r.URL.Query().Get("channel"))
		if e != nil {
			http.Error(w, "channel invalid", 400)
			return
		}
		u.Lock()
		if ch < 0 || ch >= len(u.all) {
			u.Unlock()
			http.Error(w, "channel not found", 404)
			return
		}
		a := u.all[ch]
		u.Unlock()
		interval, e := strconv.ParseFloat(r.URL.Query().Get("interval"), 64)
		if e != nil {
			interval = 0.001
		}
		window, e := strconv.Atoi(r.URL.Query().Get("window"))
		if e != nil {
			window = 11
		}
		pts, e := dqdvSeries(a, r.URL.Query().Get("cycle"), r.URL.Query().Get("step"), r.URL.Query().Get("mode"), interval, window)
		if e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		sendJSON(w, map[string]any{"points": pts, "count": len(pts), "stepSelection": "최장 충방전 Step 1개 선택", "note": "CC 구간을 우선 선택해야 해석에 유리합니다. 단조 전압 필터 후 보간·미분·SG 처리를 수행합니다."})
	})
	mux.HandleFunc("/api/exportDQ", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) || r.Method != "POST" {
			return
		}
		var q struct {
			Channel           int `json:"channel"`
			Cycle, Step, Mode string
			Interval          float64
			Window            int
		}
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 10000)).Decode(&q); e != nil {
			http.Error(w, "bad request", 400)
			return
		}
		u.Lock()
		if q.Channel < 0 || q.Channel >= len(u.all) {
			u.Unlock()
			http.Error(w, "channel not found", 404)
			return
		}
		a := u.all[q.Channel]
		out := u.output
		u.Unlock()
		pts, e := dqdvSeries(a, q.Cycle, q.Step, q.Mode, q.Interval, q.Window)
		if e != nil {
			http.Error(w, e.Error(), 422)
			return
		}
		name := fmt.Sprintf("PNE_dQdV_CH%d_Cycle%s_Step%s.csv", q.Channel+1, q.Cycle, q.Step)
		name = strings.ReplaceAll(name, " ", "_")
		output := filepath.Join(out, name)
		if e := writeDQCSV(output, pts); e != nil {
			http.Error(w, e.Error(), 500)
			return
		}
		sendJSON(w, map[string]any{"path": output, "count": len(pts)})
	})
	mux.HandleFunc("/api/quit", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) || r.Method != "POST" {
			return
		}
		sendJSON(w, map[string]any{"ok": true})
		go func() {
			time.Sleep(400 * time.Millisecond)
			u.Lock()
			tmp := u.uploadedInput
			u.Unlock()
			if tmp != "" {
				os.Remove(tmp)
			}
			os.Exit(0)
		}()
	})
	mux.HandleFunc("/api/openReport", func(w http.ResponseWriter, r *http.Request) {
		if !safe(w, r) {
			return
		}
		if r.Method != "POST" {
			http.Error(w, "method", 405)
			return
		}
		u.Lock()
		target := filepath.Join(u.output, "PNE_Analysis_Report.html")
		u.Unlock()
		if _, err := os.Stat(target); err != nil {
			http.Error(w, "report unavailable", 404)
			return
		}
		openBrowser("file:///" + filepath.ToSlash(target))
		sendJSON(w, map[string]any{"ok": true})
	})
	registerFullAPI(mux, u, safe)
	listener, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := r.Host
		if !strings.HasPrefix(host, "127.0.0.1:") && !strings.HasPrefix(host, "localhost:") {
			http.Error(w, "local only", 403)
			return
		}
		mux.ServeHTTP(w, r)
	}), ReadHeaderTimeout: 10 * time.Second}
	url := "http://" + listener.Addr().String() + "/"
	go openBrowser(url)
	server.Serve(listener)
	u.Lock()
	tmp := u.uploadedInput
	u.Unlock()
	if tmp != "" {
		os.Remove(tmp)
	}
}
func sendJSON(w http.ResponseWriter, x any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(x)
}
func openBrowser(url string) {
	if runtime.GOOS == "windows" {
		exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", url).Start()
	} else if runtime.GOOS == "darwin" {
		exec.Command("open", url).Start()
	} else {
		exec.Command("xdg-open", url).Start()
	}
}
func defaultOutputFor(in string) string { return `D:\Downloads` }
func defaultOutputForDrop() string      { return `D:\Downloads` }
func nativePick(t string) (string, error) {
	if runtime.GOOS != "windows" {
		return "", fmt.Errorf("Windows 전용 경로 선택 기능입니다")
	}
	// Dialog runs in a hidden-window PowerShell process. An invisible TOPMOST owner
	// keeps the Windows common dialog in front of Edge without flashing a console.
	pre := `$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; ` +
		`$o=New-Object System.Windows.Forms.Form; $o.TopMost=$true; $o.ShowInTaskbar=$false; ` +
		`$o.Size=New-Object System.Drawing.Size(1,1); $o.StartPosition='CenterScreen'; ` +
		`$o.Opacity=0; $o.Show(); try { `
	post := ` } finally { $o.Close(); $o.Dispose() }`
	var ps string
	switch t {
	case "zip":
		ps = pre + `$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='PNE ZIP 파일 선택'; ` +
			`$d.Filter='ZIP files (*.zip)|*.zip|All files (*.*)|*.*'; ` +
			`if(Test-Path 'D:\Downloads'){$d.InitialDirectory='D:\Downloads'}; ` +
			`if ($d.ShowDialog($o) -eq 'OK') {[Console]::Write([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($d.FileName)))}; $d.Dispose();` + post
	case "folder", "out":
		ps = pre + `$d=New-Object System.Windows.Forms.FolderBrowserDialog; ` +
			`$d.Description='PNE 폴더 또는 결과 저장 폴더 선택'; ` +
			`if(Test-Path 'D:\Downloads'){$d.SelectedPath='D:\Downloads'}; ` +
			`if ($d.ShowDialog($o) -eq 'OK') {[Console]::Write([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($d.SelectedPath)))}; $d.Dispose();` + post
	default:
		return "", fmt.Errorf("잘못된 선택 유형")
	}
	cmdBytes := utf16.Encode([]rune(ps))
	b := make([]byte, 2*len(cmdBytes))
	for i, c := range cmdBytes {
		binary.LittleEndian.PutUint16(b[i*2:], c)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-STA", "-EncodedCommand", base64.StdEncoding.EncodeToString(b))
	hideChildWindow(cmd)
	out, e := cmd.CombinedOutput()
	if ctx.Err() != nil {
		return "", fmt.Errorf("선택 창 응답 시간 초과: %w", ctx.Err())
	}
	if e != nil {
		return "", fmt.Errorf("Windows 선택창 실행 실패: %s (%w)", strings.TrimSpace(string(out)), e)
	}
	encoded := strings.TrimSpace(string(out))
	if encoded == "" {
		return "", nil
	}
	raw, e := base64.StdEncoding.DecodeString(encoded)
	if e != nil {
		return "", fmt.Errorf("파일 선택 경로 디코딩 실패: %w", e)
	}
	if len(raw)%2 != 0 {
		return "", fmt.Errorf("파일 선택 경로 길이 이상")
	}
	words := make([]uint16, len(raw)/2)
	for i := range words {
		words[i] = binary.LittleEndian.Uint16(raw[i*2:])
	}
	return string(utf16.Decode(words)), nil
}
func chartPoints(a analyzed, cycle, step, metric string, modes ...string) ([][2]float64, error) {
	mode := "all"
	if len(modes) > 0 {
		mode = modes[0]
	}
	return chartPointsAxis(a, cycle, step, metric, mode, "time")
}
func chartPointsAxis(a analyzed, cycle, step, metric, mode, xField string) ([][2]float64, error) {
	rc, e := a.pair.cyc.open()
	if e != nil {
		return nil, e
	}
	defer rc.Close()
	if _, e = io.CopyN(io.Discard, rc, headerBytes); e != nil {
		return nil, e
	}
	samples := make([][2]float64, 0, 2000)
	const maxPoints = 2400
	stepCandidates := make([]segment, 0)
	for _, s := range a.segments {
		if s.status != "MATCHED" && s.status != "REVIEW" {
			continue
		}
		if s.row < 0 {
			continue
		}
		r := a.csvRows[s.row]
		if cycle != "" && r.curCycle != cycle {
			continue
		}
		if step != "" && r.num != step {
			continue
		}
		typ := strings.ToLower(r.typ)
		isCharge := strings.Contains(typ, "충전") || strings.Contains(typ, "charge")
		isDischarge := strings.Contains(typ, "방전") || strings.Contains(typ, "discharge")
		isRest := strings.Contains(typ, "휴지") || strings.Contains(typ, "rest")
		if mode == "charge" && !isCharge || mode == "discharge" && !isDischarge || mode == "rest" && !isRest {
			continue
		}
		stepCandidates = append(stepCandidates, s)
	}
	total := int64(0)
	for _, s := range stepCandidates {
		total += s.count
	}
	stride := int64(1)
	if total > maxPoints {
		stride = (total + maxPoints - 1) / maxPoints
	}
	// Scan the original file; leave untouched. Only requested matched segments are plotted.
	buf := make([]byte, recordBytes)
	candidate := 0
	selected := int64(0)
	// Calculate cumulative time for the selected cycle; don't use the raw record index as time.
	// An individual StepTime resets at each Step boundary.
	cumulative := make([]float64, len(stepCandidates))
	for j := 1; j < len(stepCandidates); j++ {
		cumulative[j] = cumulative[j-1] + math.Max(0, float64(stepCandidates[j-1].lastRec.stepTime))
	}
	for n := int64(1); n <= a.records; n++ {
		if _, e = io.ReadFull(rc, buf); e != nil {
			return nil, e
		}
		for candidate < len(stepCandidates) && n > stepCandidates[candidate].last {
			candidate++
		}
		if candidate >= len(stepCandidates) {
			break
		}
		if n < stepCandidates[candidate].first {
			continue
		}
		if selected%stride != 0 && n != stepCandidates[candidate].last {
			selected++
			continue
		}
		r := decodeRec(buf)
		x := cumulative[candidate] + math.Max(0, float64(r.stepTime))
		switch xField {
		case "capacity":
			x = r.cap / 1000
		case "voltage":
			x = r.v / 1000
		case "", "time":
		default:
			return nil, fmt.Errorf("지원하지 않는 X축: %s", xField)
		}
		y := 0.0
		switch metric {
		case "voltage":
			y = r.v / 1000
		case "current":
			y = r.i / 1000
		case "capacity":
			y = r.cap / 1000
		case "temperature":
			y = r.temp
		case "energy":
			y = r.energy / 1000
		default:
			y = r.v / 1000
		}
		if isFinite(x) && isFinite(y) && math.Abs(x) < 1e14 && math.Abs(y) < 1e14 {
			samples = append(samples, [2]float64{x, y})
		}
		selected++
	}
	return samples, nil
}
