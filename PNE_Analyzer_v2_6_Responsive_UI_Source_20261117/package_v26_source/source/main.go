// PNE Battery Analyzer v2.2
// Offline ZIP/directory reader and CSV exporter. Standard Go library only.
// PNE layout is a user-validated working hypothesis, not a vendor specification.
package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/csv"
	"errors"
	"flag"
	"fmt"
	"html"
	"io"
	"math"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"
)

const (
	headerBytes       = 72
	recordBytes       = 176
	maxCSVBytes int64 = 120 << 20
	maxRawBytes int64 = 4 << 30
	maxFiles          = 50000
)

type sourceFile struct {
	name, group, base string
	size              int64
	open              func() (io.ReadCloser, error)
}
type pair struct {
	name, dir, id string
	cyc, csv      sourceFile
}
type stepRow struct {
	num, typ, state, curCycle, totalCycle, start, end string
	seconds, volt, amp, capacity, energy              float64
	hasTime, hasVolt, hasAmp, hasCap                  bool
}
type rec struct{ index, stepTime, cvTime, v, i, cap, charge, discharge, energy, temp, imp10, power, imp01, imp1, imp5, imp30, imp60, dateRaw float64 }
type segment struct {
	first, last          int64
	firstRec, lastRec    rec
	count                int64
	row                  int
	cost, dt, dv, di, dq float64
	status               string
}
type analyzed struct {
	pair                                            pair
	size, records, remainder                        int64
	segments                                        []segment
	csvRows                                         []stepRow
	matched, review, unmatched, orphan, initialized int
	note                                            []string
	id, stepCSV, rawCSV                             string
}

var channelPattern = regexp.MustCompile(`(?i)M\d+Ch\d+(?:\[\d+\])?`)
var disallowed = regexp.MustCompile(`[^a-zA-Z0-9._-]`)

func main() {
	if len(os.Args) == 1 || (len(os.Args) > 1 && os.Args[1] != "--cli" && !strings.HasPrefix(os.Args[1], "-")) {
		guiMain(os.Args[1:])
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "--cli" {
		os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	}
	input := flag.String("input", "", "ZIP 파일 또는 상위 폴더 경로 (또는 ZIP을 EXE에 드래그)")
	out := flag.String("output", "", "출력 폴더 경로, 기본은 Documents/PNE_Analysis_날짜")
	raw := flag.Bool("raw", false, "전체 Raw Data CSV 생성 여부 (기본 비활성화)")
	pause := flag.Bool("pause", true, "실행 종료 시 Enter 대기")
	flag.Parse()
	fmt.Println("===========================================================")
	fmt.Println(" PNE CYC + StepEnd Multi-Channel Analyzer v2.2")
	fmt.Println(" OFFLINE / NO UPLOAD / ORIGINAL FILES READ ONLY")
	fmt.Println("===========================================================")
	fmt.Println("72B header + 176B record and field offsets are experimental.")
	in := strings.TrimSpace(*input)
	if in == "" && flag.NArg() > 0 {
		in = flag.Arg(0)
	}
	reader := bufio.NewReader(os.Stdin)
	if in == "" {
		fmt.Println("ZIP 파일 또는 상위 폴더 경로를 붙여넣으세요.")
		fmt.Print("입력 경로 > ")
		line, _ := reader.ReadString('\n')
		in = strings.TrimSpace(line)
	}
	in = strings.Trim(strings.TrimSpace(in), "\"' ")
	if in == "" {
		fmt.Println("오류: 입력 경로 없음")
		finish(*pause, reader)
		os.Exit(1)
	}
	output := strings.Trim(strings.TrimSpace(*out), "\"' ")
	if output == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			home = "."
		}
		defaultOutput := filepath.Join(home, "Documents", "PNE_Analysis_"+time.Now().Format("20060102_150405"))
		fmt.Println("주의: 결과에는 회사 Raw 측정값이 포함됩니다. 승인된 로컬/사내 보관 위치를 사용하세요.")
		fmt.Println("OneDrive 등 자동 동기화 폴더는 피해야 합니다.")
		fmt.Print("결과 저장 폴더 [Enter = ", defaultOutput, "] > ")
		line, _ := reader.ReadString('\n')
		output = strings.Trim(strings.TrimSpace(line), "\"' ")
		if output == "" {
			output = defaultOutput
		}
	}
	if err := run(in, output, *raw); err != nil {
		fmt.Println("\nERROR:", err)
		finish(*pause, reader)
		os.Exit(1)
	}
	fmt.Println("\n완료. 보고서:", filepath.Join(output, "PNE_Analysis_Report.html"))
	fmt.Println("보고서와 CSV는 회사 PC에만 저장되며 외부 전송하지 않습니다.")
	finish(*pause, reader)
}
func finish(p bool, r *bufio.Reader) {
	if p {
		fmt.Print("Enter를 누르면 종료합니다... ")
		_, _ = r.ReadString('\n')
	}
}

func run(input, output string, wantRaw bool) error {
	_, err := runDetailed(input, output, wantRaw)
	return err
}
func runDetailed(input, output string, wantRaw bool) ([]analyzed, error) {
	pairs, cleanup, err := findPairs(input)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return nil, err
	}
	if len(pairs) == 0 {
		return nil, errors.New("채널 폴더에서 .cyc 와 *_StepEnd.csv 쌍을 찾지 못했습니다. ZIP 내부 경로를 확인하세요")
	}
	if err = os.MkdirAll(output, 0750); err != nil {
		return nil, fmt.Errorf("출력 폴더 생성: %w", err)
	}
	fmt.Printf("발견한 채널 쌍: %d개\n", len(pairs))
	var all []analyzed
	for k, p := range pairs {
		fmt.Printf("[%d/%d] %s ...\n", k+1, len(pairs), p.name)
		a := analyzed{pair: p, id: fmt.Sprintf("CH%03d_%s", k+1, sanitize(p.id))}
		a.csvRows, err = readStepCSV(p.csv)
		if err != nil {
			a.note = append(a.note, "StepEnd CSV 읽기 실패: "+err.Error())
		}
		sz := p.cyc.size
		a.size = sz
		if sz < headerBytes {
			a.note = append(a.note, "CYC 크기가 72바이트보다 작습니다")
		}
		if sz >= headerBytes {
			a.records = (sz - headerBytes) / recordBytes
			a.remainder = (sz - headerBytes) % recordBytes
		}
		if a.remainder != 0 {
			a.note = append(a.note, fmt.Sprintf("헤더 72B·레코드 176B 가설과 불일치: 끝에 %d바이트 잔여", a.remainder))
		}
		if sz > maxRawBytes {
			a.note = append(a.note, "CYC 파일 크기 제한(4GiB) 초과")
		}
		if a.records > 0 && a.remainder == 0 && sz <= maxRawBytes {
			a.segments, err = readSegments(p.cyc)
			if err != nil {
				a.note = append(a.note, "CYC 분석 실패: "+err.Error())
			}
			classifyInitial(&a)
			if len(a.csvRows) > 0 && len(a.segments) > 0 {
				matchSteps(&a)
			}
			a.countStatus()
			a.stepCSV = a.id + "_Step_Matching.csv"
			if err := exportSteps(filepath.Join(output, a.stepCSV), &a); err != nil {
				a.note = append(a.note, "Step 보고서 실패: "+err.Error())
				a.stepCSV = ""
			}
			if wantRaw {
				a.rawCSV = a.id + "_Raw_Data.csv"
				if err := exportRaw(filepath.Join(output, a.rawCSV), &a); err != nil {
					a.note = append(a.note, "Raw CSV 실패: "+err.Error())
					a.rawCSV = ""
				}
			}
		}
		if len(a.csvRows) == 0 {
			a.note = append(a.note, "StepEnd 행을 읽지 못했으므로 Step No/Cycle 결합 불가")
		}
		if len(a.segments) == 0 {
			a.note = append(a.note, "CYC Step 구간을 생성하지 못했습니다")
		}
		fmt.Printf("    Raw %d행, CYC 구간 %d개, StepEnd %d행 / 매칭 %d, 확인필요 %d, 미매칭 %d, CSV 잔여 %d\n", a.records, len(a.segments), len(a.csvRows), a.matched, a.review, a.unmatched, a.orphan)
		all = append(all, a)
	}
	report := renderReport(input, all)
	if err := os.WriteFile(filepath.Join(output, "PNE_Analysis_Report.html"), []byte(report), 0640); err != nil {
		return nil, err
	}
	if err := exportOverview(filepath.Join(output, "PNE_Channels_Summary.csv"), all); err != nil {
		return nil, err
	}
	return all, nil
}

// zipEntryCloser owns BOTH the uncompressed entry and the ZIP file descriptor.
// It is essential for browsing charts after findPairs()'s initial ZIP has closed.
type zipEntryCloser struct {
	io.ReadCloser
	archive *os.File
}

func (c *zipEntryCloser) Close() error {
	err := c.ReadCloser.Close()
	e2 := c.archive.Close()
	if err != nil {
		return err
	}
	return e2
}
func openZIPEntry(zipPath, entryName string) (io.ReadCloser, error) {
	f, err := os.Open(zipPath)
	if err != nil {
		return nil, fmt.Errorf("ZIP 다시 열기 실패 (%s): %w", filepath.Base(zipPath), err)
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, err
	}
	zr, err := zip.NewReader(f, st.Size())
	if err != nil {
		f.Close()
		return nil, err
	}
	for _, zf := range zr.File {
		if zf.Name == entryName {
			rc, err := zf.Open()
			if err != nil {
				f.Close()
				return nil, err
			}
			return &zipEntryCloser{ReadCloser: rc, archive: f}, nil
		}
	}
	f.Close()
	return nil, fmt.Errorf("ZIP에서 항목을 다시 찾을 수 없습니다: %s", entryName)
}

func findPairs(input string) ([]pair, func(), error) {
	st, err := os.Stat(input)
	if err != nil {
		return nil, nil, err
	}
	files := []sourceFile{}
	var cleanup func()
	if st.IsDir() {
		err = filepath.WalkDir(input, func(fp string, d os.DirEntry, e error) error {
			if e != nil {
				return e
			}
			if d.IsDir() {
				return nil
			}
			if len(files) > maxFiles {
				return errors.New("파일이 너무 많습니다")
			}
			name := d.Name()
			if !wanted(name) {
				return nil
			}
			rel, e := filepath.Rel(input, fp)
			if e != nil {
				return e
			}
			rel = filepath.ToSlash(rel)
			inf, e := d.Info()
			if e != nil {
				return e
			}
			if !inf.Mode().IsRegular() {
				return nil
			}
			full := fp
			files = append(files, sourceFile{name: rel, group: path.Dir(rel), base: name, size: inf.Size(), open: func() (io.ReadCloser, error) { return os.Open(full) }})
			return nil
		})
		if err != nil {
			return nil, nil, err
		}
	} else {
		if !strings.EqualFold(filepath.Ext(input), ".zip") {
			return nil, nil, errors.New("입력이 파일인 경우 ZIP(.zip)이어야 합니다. 또는 데이터가 있는 폴더를 지정해 주세요")
		}
		f, e := os.Open(input)
		if e != nil {
			return nil, nil, e
		}
		cleanup = func() { _ = f.Close() }
		zr, e := zip.NewReader(f, st.Size())
		if e != nil {
			return nil, cleanup, fmt.Errorf("ZIP을 열 수 없습니다: %w", e)
		}
		if len(zr.File) > maxFiles {
			return nil, cleanup, errors.New("ZIP 항목이 너무 많습니다")
		}
		for _, zf := range zr.File {
			norm := strings.ReplaceAll(zf.Name, "\\", "/")
			if strings.HasPrefix(norm, "/") || strings.HasPrefix(norm, "//") || strings.Contains(norm, ":") {
				continue
			}
			parts := strings.Split(norm, "/")
			bad := false
			for _, p := range parts {
				if p == ".." || p == "." || p == "" {
					bad = true
					break
				}
			}
			if bad || path.Clean(norm) != norm || zf.FileInfo().IsDir() || !zf.Mode().IsRegular() {
				continue
			}
			if !wanted(path.Base(norm)) {
				continue
			}
			if zf.UncompressedSize64 > uint64(maxRawBytes) {
				continue
			}
			item := zf
			// Reopen ZIP per source read: findPairs() closes its initial ZIP once runDetailed returns.
			// The graph viewer needs to read selected records after analysis has completed.
			zipPath := input
			zipEntry := norm
			files = append(files, sourceFile{name: norm, group: path.Dir(norm), base: path.Base(norm), size: int64(item.UncompressedSize64), open: func() (io.ReadCloser, error) {
				return openZIPEntry(zipPath, zipEntry)
			}})
		}
	}
	byGroup := make(map[string][]sourceFile)
	for _, f := range files {
		byGroup[f.group] = append(byGroup[f.group], f)
	}
	keys := make([]string, 0, len(byGroup))
	for k := range byGroup {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var pairs []pair
	for _, g := range keys {
		gs := byGroup[g]
		cycs := []sourceFile{}
		csvs := []sourceFile{}
		for _, f := range gs {
			if strings.EqualFold(filepath.Ext(f.base), ".cyc") {
				cycs = append(cycs, f)
			} else if strings.HasSuffix(strings.ToLower(f.base), ".csv") && strings.Contains(strings.ToLower(f.base), "stepend") {
				csvs = append(csvs, f)
			}
		}
		sort.Slice(cycs, func(i, j int) bool { return cycs[i].base < cycs[j].base })
		for _, c := range cycs {
			target := strings.TrimSuffix(strings.ToLower(c.base), ".cyc")
			match := -1
			for j, v := range csvs {
				b := strings.ToLower(v.base)
				if strings.HasPrefix(b, target) && strings.HasSuffix(b, "_stepend.csv") {
					match = j
					break
				}
			}
			if match < 0 && len(cycs) == 1 && len(csvs) == 1 {
				match = 0
			}
			if match < 0 {
				continue
			}
			id := channelPattern.FindString(g)
			if id == "" {
				id = channelPattern.FindString(c.base)
			}
			if id == "" {
				id = path.Base(g)
			}
			if id == "" || id == "." {
				id = "Channel"
			}
			pairs = append(pairs, pair{name: g, dir: g, id: id, cyc: c, csv: csvs[match]})
			csvs = append(csvs[:match], csvs[match+1:]...)
		}
	}
	return pairs, cleanup, nil
}
func wanted(name string) bool {
	lo := strings.ToLower(name)
	return strings.HasSuffix(lo, ".cyc") || (strings.HasSuffix(lo, ".csv") && strings.Contains(lo, "stepend"))
}
func sanitize(s string) string {
	s = disallowed.ReplaceAllString(s, "_")
	if len(s) > 90 {
		s = s[:90]
	}
	if s == "" {
		s = "channel"
	}
	return s
}

func readSegments(f sourceFile) ([]segment, error) {
	rc, err := f.open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	if _, err = io.CopyN(io.Discard, rc, headerBytes); err != nil {
		return nil, err
	}
	b := make([]byte, recordBytes)
	segments := []segment{}
	var cur segment
	var prev rec
	var have bool
	for n := int64(1); ; n++ {
		_, err = io.ReadFull(rc, b)
		if err == io.EOF {
			break
		}
		if err == io.ErrUnexpectedEOF {
			return nil, errors.New("잘못된 레코드 길이")
		}
		if err != nil {
			return nil, err
		}
		r := decodeRec(b)
		if !isFinite(r.stepTime) || !isFinite(r.v) || !isFinite(r.i) {
			return nil, fmt.Errorf("%d번 레코드 숫자 해석 비정상", n)
		}
		reset := have && r.stepTime+math.Max(.02, prev.stepTime*0.00001) < prev.stepTime && prev.stepTime > 0.05
		if reset {
			segments = append(segments, cur)
			if len(segments) > 400000 {
				return nil, errors.New("Step 구간이 비정상적으로 많습니다")
			}
			have = false
		}
		if !have {
			cur = segment{first: n, last: n, firstRec: r, lastRec: r, count: 1, row: -1, status: "UNMATCHED"}
			have = true
		} else {
			cur.last = n
			cur.lastRec = r
			cur.count++
		}
		prev = r
	}
	if have {
		segments = append(segments, cur)
	}
	return segments, nil
}
func f32(b []byte, off int) float64 {
	return float64(math.Float32frombits(binary.LittleEndian.Uint32(b[off : off+4])))
}
func decodeRec(b []byte) rec {
	return rec{
		index: f32(b, 0), stepTime: f32(b, 4), cvTime: f32(b, 8), v: f32(b, 16), i: f32(b, 20), cap: f32(b, 24), charge: f32(b, 32), discharge: f32(b, 36), energy: f32(b, 40), temp: f32(b, 64), imp10: f32(b, 68), power: f32(b, 72), imp01: f32(b, 112), imp1: f32(b, 116), imp5: f32(b, 120), imp30: f32(b, 124), imp60: f32(b, 128), dateRaw: f32(b, 140)}
}

func readStepCSV(f sourceFile) ([]stepRow, error) {
	if f.size <= 0 || f.size > maxCSVBytes {
		return nil, fmt.Errorf("CSV 크기 제한(120MiB) 또는 빈 파일: %d bytes", f.size)
	}
	rc, err := f.open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	data, err := io.ReadAll(io.LimitReader(rc, maxCSVBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maxCSVBytes {
		return nil, errors.New("CSV 크기 초과")
	}
	s := decodeText(data)
	s = strings.TrimPrefix(s, "\ufeff")
	lines := strings.Split(strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n"), "\n")
	headerIdx := -1
	for i, line := range lines {
		if strings.Contains(strings.ToLower(line), "stepno") && strings.Contains(strings.ToLower(line), "voltage") {
			headerIdx = i
			break
		}
		if i > 100 {
			break
		}
	}
	if headerIdx < 0 {
		return nil, errors.New("StepNo와 Voltage 열이 있는 CSV 헤더를 찾지 못했습니다")
	}
	sep := ','
	h := lines[headerIdx]
	if strings.Count(h, "\t") > strings.Count(h, ",") {
		sep = '\t'
	} else if strings.Count(h, ";") > strings.Count(h, ",") {
		sep = ';'
	}
	r := csv.NewReader(strings.NewReader(strings.Join(lines[headerIdx:], "\n")))
	r.Comma = sep
	r.FieldsPerRecord = -1
	r.LazyQuotes = true
	rows, err := r.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) < 2 {
		return nil, errors.New("Step End 데이터 행이 없습니다")
	}
	col := map[string]int{}
	for i, c := range rows[0] {
		col[normal(c)] = i
	}
	get := func(row []string, name string) string {
		ix, ok := col[normal(name)]
		if !ok || ix >= len(row) {
			return ""
		}
		return strings.TrimSpace(row[ix])
	}
	var result []stepRow
	for _, row := range rows[1:] {
		sn := get(row, "StepNo")
		if sn == "" || strings.EqualFold(sn, "StepNo") {
			continue
		}
		st := stepRow{num: sn, typ: get(row, "StepType"), state: get(row, "StateCode"), curCycle: get(row, "CurCycle"), totalCycle: get(row, "TotalCycle"), start: get(row, "StartTime"), end: get(row, "EndTime")}
		st.seconds, st.hasTime = parseDuration(get(row, "StepTime"))
		st.volt, st.hasVolt = parseFloat(get(row, "Voltage"))
		st.amp, st.hasAmp = parseFloat(get(row, "Current"))
		st.capacity, st.hasCap = parseFloat(get(row, "Capacity"))
		st.energy, _ = parseFloat(get(row, "WattHour"))
		result = append(result, st)
	}
	return result, nil
}
func normal(s string) string {
	s = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(s, "\ufeff")))
	return strings.NewReplacer(" ", "", "_", "", "-", "", "\t", "").Replace(s)
}
func parseFloat(s string) (float64, bool) {
	s = strings.TrimSpace(strings.ReplaceAll(s, ",", ""))
	if s == "" {
		return 0, false
	}
	v, e := strconv.ParseFloat(s, 64)
	return v, e == nil && isFinite(v)
}
func parseDuration(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	days := 0.0
	if k := strings.Index(strings.ToLower(s), "d"); k >= 0 {
		n, e := strconv.ParseFloat(strings.TrimSpace(s[:k]), 64)
		if e != nil {
			return 0, false
		}
		days = n
		s = strings.TrimSpace(s[k+1:])
	}
	sp := strings.Split(s, ":")
	if len(sp) == 1 {
		n, ok := parseFloat(sp[0])
		return days*86400 + n, ok
	}
	if len(sp) != 3 {
		return 0, false
	}
	h, e1 := strconv.ParseFloat(strings.TrimSpace(sp[0]), 64)
	m, e2 := strconv.ParseFloat(strings.TrimSpace(sp[1]), 64)
	sec, e3 := strconv.ParseFloat(strings.TrimSpace(sp[2]), 64)
	if e1 != nil || e2 != nil || e3 != nil {
		return 0, false
	}
	return days*86400 + h*3600 + m*60 + sec, true
}

// CSV may have Windows Korean (CP949) encoding; Windows API conversion lives in wincharset_windows.go.
func decodeText(b []byte) string {
	if len(b) >= 2 && b[0] == 0xff && b[1] == 0xfe {
		return utf16Decode(b[2:], true)
	}
	if len(b) >= 2 && b[0] == 0xfe && b[1] == 0xff {
		return utf16Decode(b[2:], false)
	}
	if len(b) >= 3 && bytes.Equal(b[:3], []byte{0xef, 0xbb, 0xbf}) {
		return string(b[3:])
	}
	if utf8.Valid(b) {
		return string(b)
	}
	return decodeCP949(b)
}
func utf16Decode(b []byte, little bool) string {
	units := make([]uint16, 0, len(b)/2)
	for i := 0; i+1 < len(b); i += 2 {
		if little {
			units = append(units, binary.LittleEndian.Uint16(b[i:]))
		} else {
			units = append(units, binary.BigEndian.Uint16(b[i:]))
		}
	}
	return string(utf16.Decode(units))
}

func cost(a segment, b stepRow) (float64, float64, float64, float64, float64) {
	if a.status == "INITIALIZATION" {
		return 1e6, math.NaN(), math.NaN(), math.NaN(), math.NaN()
	}
	s := a.lastRec
	c := 0.0
	f := 0
	dt, dv, di, dq := math.NaN(), math.NaN(), math.NaN(), math.NaN()
	if b.hasTime {
		dt = math.Abs(s.stepTime - b.seconds)
		norm := math.Max(1.2, 0.01*math.Max(b.seconds, 1))
		c += math.Min(4, dt/norm)
		f++
	}
	if b.hasVolt {
		dv = math.Abs(s.v/1000 - b.volt)
		c += math.Min(4, dv/0.025)
		f++
	}
	if b.hasAmp {
		di = math.Abs(s.i/1000 - b.amp)
		c += math.Min(3, di/math.Max(.3, math.Abs(b.amp)*0.07))
		f++
	}
	if b.hasCap {
		dq = math.Abs(s.cap/1000 - b.capacity)
		c += math.Min(3, dq/math.Max(.012, math.Abs(b.capacity)*0.04))
		f++
	}
	if f == 0 {
		return 9, dt, dv, di, dq
	}
	if f < 3 {
		c += 2
	}
	return c, dt, dv, di, dq
}

func plausible(r rec) bool {
	return isFinite(r.stepTime) && r.stepTime >= 0 && r.stepTime < 1e9 && isFinite(r.v) && r.v > -1000 && r.v < 15000 && isFinite(r.i) && math.Abs(r.i) < 1e7
}

// Keep the source record intact for audit, but remove a clearly malformed singleton
// before DP matches. This cannot prove it is a vendor header.
func classifyInitial(a *analyzed) {
	if len(a.segments) < 2 {
		return
	}
	first := a.segments[0]
	next := a.segments[1]
	if first.first != 1 || first.count != 1 || plausible(first.lastRec) || !plausible(next.firstRec) {
		return
	}
	a.segments[0].status = "INITIALIZATION"
	a.note = append(a.note, "첫 레코드의 값이 물리 범위 밖이고 다음 구간이 정상이라 INITIALIZATION으로 분류함. 원본은 보존, 정량 분석에서 제외")
}
func matchSteps(a *analyzed) {
	n, m := len(a.segments), len(a.csvRows)
	if n == 0 || m == 0 {
		return
	}
	if int64(n)*int64(m) > 5000000 {
		a.note = append(a.note, "매칭 구간이 너무 많아 전체 DP 대신 근접 순서 매칭으로 대체")
		greedyMatch(a)
		return
	}
	const skip = 4.0
	w := m + 1
	dp := make([]float32, (n+1)*w)
	ptr := make([]uint8, (n+1)*w)
	for i := 1; i <= n; i++ {
		dp[i*w] = float32(i * skip)
		ptr[i*w] = 1
	}
	for j := 1; j <= m; j++ {
		dp[j] = float32(j * skip)
		ptr[j] = 2
	}
	for i := 1; i <= n; i++ {
		for j := 1; j <= m; j++ {
			c, _, _, _, _ := cost(a.segments[i-1], a.csvRows[j-1])
			best := float64(dp[(i-1)*w+j-1]) + c
			action := uint8(0)
			if t := float64(dp[(i-1)*w+j]) + skip; t < best {
				best = t
				action = 1
			}
			if t := float64(dp[i*w+j-1]) + skip; t < best {
				best = t
				action = 2
			}
			dp[i*w+j] = float32(best)
			ptr[i*w+j] = action
		}
	}
	for i, j := n, m; i > 0 || j > 0; {
		switch ptr[i*w+j] {
		case 0:
			c, dt, dv, di, dq := cost(a.segments[i-1], a.csvRows[j-1])
			setMatch(&a.segments[i-1], j-1, c, dt, dv, di, dq)
			i--
			j--
		case 1:
			i--
		case 2:
			j--
		}
	}
}
func greedyMatch(a *analyzed) {
	j := 0
	for i := range a.segments {
		bestJ := -1
		bestC := 7.5
		for k := j; k < len(a.csvRows) && k <= j+5; k++ {
			c, _, _, _, _ := cost(a.segments[i], a.csvRows[k])
			if c < bestC {
				bestC = c
				bestJ = k
			}
		}
		if bestJ >= 0 {
			c, dt, dv, di, dq := cost(a.segments[i], a.csvRows[bestJ])
			setMatch(&a.segments[i], bestJ, c, dt, dv, di, dq)
			j = bestJ + 1
		}
	}
}
func setMatch(s *segment, j int, c, dt, dv, di, dq float64) {
	if s.status == "INITIALIZATION" {
		return
	}
	s.row = j
	s.cost = c
	s.dt = dt
	s.dv = dv
	s.di = di
	s.dq = dq
	if c <= 2.4 && (math.IsNaN(dt) || dt <= math.Max(2.0, .015*s.lastRec.stepTime)) && (math.IsNaN(dv) || dv <= .02) && (math.IsNaN(di) || di <= math.Max(.25, math.Abs(s.lastRec.i/1000)*.05)) && (math.IsNaN(dq) || dq <= math.Max(.012, math.Abs(s.lastRec.cap/1000)*.04)) {
		s.status = "MATCHED"
	} else {
		s.status = "REVIEW"
	}
}
func (a *analyzed) countStatus() {
	hit := make(map[int]bool)
	for _, s := range a.segments {
		switch s.status {
		case "MATCHED":
			a.matched++
		case "REVIEW":
			a.review++
		case "INITIALIZATION":
			a.initialized++
		default:
			a.unmatched++
		}
		if s.row >= 0 {
			hit[s.row] = true
		}
	}
	a.orphan = len(a.csvRows) - len(hit)
}

func isFinite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
func safeCell(s string) string {
	if s == "" {
		return ""
	}
	s = strings.TrimSpace(s)
	if _, err := strconv.ParseFloat(s, 64); err == nil {
		return s
	}
	if strings.ContainsRune("=+-@", rune(s[0])) {
		return "'" + s
	}
	return s
}
func wcsv(w *csv.Writer, arr []string) error {
	for i := range arr {
		arr[i] = safeCell(arr[i])
	}
	return w.Write(arr)
}
func ff(v float64) string {
	if !isFinite(v) {
		return ""
	}
	return strconv.FormatFloat(v, 'f', 6, 64)
}
func ii(v int64) string { return strconv.FormatInt(v, 10) }
func stepCSVRec(a *analyzed, s segment) []string {
	var r stepRow
	if s.row >= 0 && s.row < len(a.csvRows) {
		r = a.csvRows[s.row]
	}
	return []string{a.pair.name, s.status, ii(s.first), ii(s.last), ii(s.count), r.num, r.typ, r.state, r.curCycle, r.totalCycle, r.start, r.end, ff(s.firstRec.stepTime), ff(s.lastRec.stepTime), ff(s.lastRec.v / 1000), ff(s.lastRec.i / 1000), ff(s.lastRec.cap / 1000), ff(s.lastRec.energy / 1000), ff(r.seconds), ff(r.volt), ff(r.amp), ff(r.capacity), ff(s.cost), ff(s.dt), ff(s.dv), ff(s.di), ff(s.dq)}
}

var stepHeader = []string{"ChannelPath", "MatchStatus", "FirstRecord", "LastRecord", "RecordCount", "StepNo", "StepType", "StateCode", "CurCycle", "TotalCycle", "CSV_StartTime", "CSV_EndTime", "CYC_StartStepTime_s", "CYC_EndStepTime_s", "CYC_EndVoltage_V", "CYC_EndCurrent_A", "CYC_EndCapacity_Ah", "CYC_EndEnergy_Wh", "CSV_StepTime_s", "CSV_EndVoltage_V", "CSV_EndCurrent_A", "CSV_Capacity_Ah", "MatchCost", "DeltaTime_s", "DeltaVoltage_V", "DeltaCurrent_A", "DeltaCapacity_Ah"}

func exportSteps(dest string, a *analyzed) error {
	f, e := os.Create(dest)
	if e != nil {
		return e
	}
	defer f.Close()
	_, _ = f.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(f)
	_ = w.Write(stepHeader)
	for _, s := range a.segments {
		if e = wcsv(w, stepCSVRec(a, s)); e != nil {
			return e
		}
	}
	w.Flush()
	return w.Error()
}

var rawHeader = []string{"Record", "Index", "StepTime_s", "CVTime_s", "Voltage_mV", "Current_mA", "Capacity_mAh", "ChargeCapacity_mAh", "DischargeCapacity_mAh", "Energy_mWh", "CellTemp_C", "Imp10s_mOhm", "Power_mW", "Imp0_1s_mOhm", "Imp1s_mOhm", "Imp5s_mOhm", "Imp30s_mOhm", "Imp60s_mOhm", "DateRaw_f32_Offset140_Unverified", "MatchedStepNo", "StepType", "StateCode", "CurCycle", "TotalCycle", "MatchStatus"}

func exportRaw(dest string, a *analyzed) error {
	rc, err := a.pair.cyc.open()
	if err != nil {
		return err
	}
	defer rc.Close()
	if _, err = io.CopyN(io.Discard, rc, headerBytes); err != nil {
		return err
	}
	f, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer f.Close()
	out := bufio.NewWriterSize(f, 256*1024)
	_, _ = out.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(out)
	if err = w.Write(rawHeader); err != nil {
		return err
	}
	buf := make([]byte, recordBytes)
	si := 0
	for n := int64(1); n <= a.records; n++ {
		if _, err = io.ReadFull(rc, buf); err != nil {
			return err
		}
		for si+1 < len(a.segments) && n > a.segments[si].last {
			si++
		}
		seg := a.segments[si]
		r := decodeRec(buf)
		meta := stepRow{}
		if seg.row >= 0 && seg.row < len(a.csvRows) {
			meta = a.csvRows[seg.row]
		}
		row := []string{ii(n), ff(r.index), ff(r.stepTime), ff(r.cvTime), ff(r.v), ff(r.i), ff(r.cap), ff(r.charge), ff(r.discharge), ff(r.energy), ff(r.temp), ff(r.imp10), ff(r.power), ff(r.imp01), ff(r.imp1), ff(r.imp5), ff(r.imp30), ff(r.imp60), ff(r.dateRaw), meta.num, meta.typ, meta.state, meta.curCycle, meta.totalCycle, seg.status}
		if err = wcsv(w, row); err != nil {
			return err
		}
	}
	w.Flush()
	if err = w.Error(); err != nil {
		return err
	}
	return out.Flush()
}
func exportOverview(dest string, all []analyzed) error {
	f, e := os.Create(dest)
	if e != nil {
		return e
	}
	defer f.Close()
	_, _ = f.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(f)
	_ = w.Write([]string{"ChannelPath", "CYC_Records", "Detected_Segments", "StepEnd_Rows", "Matched", "NeedsReview", "CYC_Unmatched", "StepEnd_Unmatched", "InitialRecords", "RawCSV", "StepCSV", "Warnings"})
	for _, a := range all {
		wcsv(w, []string{a.pair.name, ii(a.records), strconv.Itoa(len(a.segments)), strconv.Itoa(len(a.csvRows)), strconv.Itoa(a.matched), strconv.Itoa(a.review), strconv.Itoa(a.unmatched), strconv.Itoa(a.orphan), strconv.Itoa(a.initialized), a.rawCSV, a.stepCSV, strings.Join(a.note, " | ")})
	}
	w.Flush()
	return w.Error()
}

func renderReport(input string, all []analyzed) string {
	var b strings.Builder
	esc := html.EscapeString
	b.WriteString(`<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; script-src 'none'; object-src 'none'"><title>PNE Multi-Channel Analysis v1.8</title><style>body{font:14px 'Malgun Gothic',Arial,sans-serif;background:#f3f7fa;color:#17324b;margin:0}header{background:#113952;color:#fff;padding:25px max(18px,calc((100vw - 1200px)/2))}h1{font-size:24px;margin:0 0 6px}h2{font-size:18px}main{max-width:1200px;margin:22px auto;padding:0 15px}.panel{border:1px solid #dce6ed;background:#fff;border-radius:12px;padding:18px;margin:16px 0}.muted{font-size:12px;color:#617485}.sub{color:#c7dfeb}.note{background:#fff6e8;padding:12px;border-left:4px solid #d99c3c;line-height:1.7}.metrics{display:flex;gap:10px;flex-wrap:wrap}.metric{flex:1;min-width:140px;background:#edf4f9;padding:13px;border-radius:8px}.metric b{font-size:21px;display:block}.scroller{overflow:auto;max-height:600px;border:1px solid #e5edf1}table{border-collapse:collapse;white-space:nowrap;width:100%;font-size:12px}th,td{border-bottom:1px solid #e5edf1;padding:9px;text-align:right}th{background:#eaf1f7;position:sticky;top:0}td:first-child,th:first-child{text-align:left}a{color:#14689d}.good{color:#1d8159;font-weight:bold}.warn{color:#a36821;font-weight:bold}details{margin-top:8px}summary{cursor:pointer;font-weight:bold;padding:10px;background:#edf4f9;border-radius:7px}.chip{display:inline-block;padding:4px 7px;background:#e8f2ea;border-radius:5px;font-size:11px;margin:4px}.chip.warn{background:#fff3dc}footer{text-align:center;padding:20px;color:#60758a}.indent{margin:10px 0}.path{word-break:break-all;font-family:Consolas,monospace;font-size:12px}</style></head><body>`)
	b.WriteString(`<header><h1>PNE CYC + StepEnd Multi-Channel Analyzer v2.2</h1><div class="sub">오프라인 분석 · 다중 채널 ZIP/폴더 검색 · 176바이트 CYC 복원 · StepEnd CSV 결합</div></header><main>`)
	totRec, totMatch, totReview, totUnmatch, totOrphan := int64(0), 0, 0, 0, 0
	for _, a := range all {
		totRec += a.records
		totMatch += a.matched
		totReview += a.review
		totUnmatch += a.unmatched
		totOrphan += a.orphan
	}
	b.WriteString(`<section class="panel"><h2>전체 현황</h2><div class="metrics">`)
	for _, v := range [][2]string{{"채널", strconv.Itoa(len(all))}, {"Raw 레코드", ii(totRec)}, {"Step 매칭", strconv.Itoa(totMatch)}, {"검토 필요", strconv.Itoa(totReview)}, {"미매칭 구간", strconv.Itoa(totUnmatch)}, {"미매칭 CSV행", strconv.Itoa(totOrphan)}} {
		b.WriteString(`<div class="metric"><b>` + esc(v[1]) + `</b><span class="muted">` + esc(v[0]) + `</span></div>`)
	}
	b.WriteString(`</div><p class="muted path">입력: ` + esc(input) + `</p><div class="note"><b>중요:</b> PNE .cyc는 공개 규격이 아닌 역분석 가설(72B 헤더, 176B 고정레코드, float32-LE)이야. 매칭은 StepTime 초기화로 분리한 구간을 CSV 종료값과 순서·시간·전압·전류·용량으로 대조한 결과이며 확정적인 제조사 해독이 아니야. <b>REVIEW/UNMATCHED</b> 데이터는 수명, dQ/dV, DCIR 정량분석에 사용하기 전에 PNE 화면에서 재확인해.</div></section>`)
	b.WriteString(`<section class="panel"><h2>채널별 상태</h2><div class="scroller"><table><thead><tr><th>채널 폴더</th><th>Raw 행</th><th>CYC 구간</th><th>StepEnd 행</th><th>일치</th><th>검토</th><th>미매칭 CYC</th><th>미매칭 CSV</th><th>초기화 레코드</th><th>상세</th></tr></thead><tbody>`)
	for i, a := range all {
		b.WriteString(fmt.Sprintf(`<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td class="good">%d</td><td class="warn">%d</td><td>%d</td><td>%d</td><td>%d</td><td><a href="#channel%d">보기</a></td></tr>`, esc(a.pair.name), a.records, len(a.segments), len(a.csvRows), a.matched, a.review, a.unmatched, a.orphan, a.initialized, i))
	}
	b.WriteString(`</tbody></table></div><p class="muted">파일: PNE_Channels_Summary.csv (채널별 전체 요약)</p></section>`)
	for i, a := range all {
		b.WriteString(fmt.Sprintf(`<section class="panel" id="channel%d"><h2>%s</h2>`, i, esc(a.pair.name)))
		b.WriteString(fmt.Sprintf(`<div class="muted">CYC %s · StepEnd %s · Raw %d행 / Step구간 %d개</div>`, esc(a.pair.cyc.base), esc(a.pair.csv.base), a.records, len(a.segments)))
		if a.stepCSV != "" {
			b.WriteString(`<p><a href="` + esc(a.stepCSV) + `">Step 매칭표 CSV 열기</a>`)
		}
		if a.rawCSV != "" {
			b.WriteString(`　|　<a href="` + esc(a.rawCSV) + `">전체 Raw Data CSV 열기</a>`)
		}
		b.WriteString(`</p>`)
		for _, s := range a.note {
			b.WriteString(`<p class="warn">⚠ ` + esc(s) + `</p>`)
		}
		b.WriteString(`<details><summary>Step 매칭표 미리보기 (최대 60행: 앞부분 및 검토 필요 구간)</summary><div class="scroller"><table><thead><tr><th>상태</th><th>Record 구간</th><th>StepNo</th><th>StepType</th><th>CurCycle</th><th>총Cycle</th><th>CYC 종료(s)</th><th>CSV 종료(s)</th><th>전압(V) CYC/CSV</th><th>용량(Ah) CYC/CSV</th><th>점수</th></tr></thead><tbody>`)
		indices := map[int]bool{}
		for j := 0; j < len(a.segments) && j < 24; j++ {
			indices[j] = true
		}
		for j, s := range a.segments {
			if s.status != "MATCHED" && len(indices) < 60 {
				indices[j] = true
			}
		}
		arr := make([]int, 0, len(indices))
		for j := range indices {
			arr = append(arr, j)
		}
		sort.Ints(arr)
		for _, j := range arr {
			s := a.segments[j]
			meta := stepRow{}
			if s.row >= 0 && s.row < len(a.csvRows) {
				meta = a.csvRows[s.row]
			}
			b.WriteString(`<tr><td>` + esc(s.status) + `</td><td>` + fmt.Sprintf("%d–%d", s.first, s.last) + `</td><td>` + esc(meta.num) + `</td><td>` + esc(meta.typ) + `</td><td>` + esc(meta.curCycle) + `</td><td>` + esc(meta.totalCycle) + `</td><td>` + esc(ff(s.lastRec.stepTime)) + `</td><td>` + esc(ff(meta.seconds)) + `</td><td>` + esc(ff(s.lastRec.v/1000)) + ` / ` + esc(ff(meta.volt)) + `</td><td>` + esc(ff(s.lastRec.cap/1000)) + ` / ` + esc(ff(meta.capacity)) + `</td><td>` + esc(ff(s.cost)) + `</td></tr>`)
		}
		b.WriteString(`</tbody></table></div><p class="muted">전체 구간은 위 Step 매칭표 CSV에서 확인할 수 있어.</p></details></section>`)
	}
	b.WriteString(`</main><footer>프로그램은 외부 네트워크 연결·업로드 기능이 없으며 ZIP/CYC 원본을 수정하지 않습니다. 회사의 소프트웨어 반입 및 데이터 보안 정책을 준수하세요.</footer></body></html>`)
	return b.String()
}
