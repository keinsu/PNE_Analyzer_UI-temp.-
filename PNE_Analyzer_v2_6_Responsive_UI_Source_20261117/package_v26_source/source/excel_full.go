package main

import (
	"archive/zip"
	"encoding/xml"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

// Minimal standards-compliant XLSX writer: inline strings, no external dependencies.
// Workbook stores a sheet for each selected DCIR time, plus raw pulse rows.
type xlsxSheet struct {
	Name string
	Rows [][]string
}

func xlsxEscape(v string) string {
	var b strings.Builder
	xml.EscapeText(&b, []byte(v))
	return b.String()
}
func excelCol(n int) string {
	s := ""
	for n > 0 {
		n--
		s = string(rune('A'+n%26)) + s
		n /= 26
	}
	return s
}
func writeXLSX(dest string, sheets []xlsxSheet) error {
	f, e := os.Create(dest)
	if e != nil {
		return e
	}
	defer f.Close()
	z := zip.NewWriter(f)
	defer z.Close()
	write := func(path, val string) error {
		w, e := z.Create(path)
		if e != nil {
			return e
		}
		_, e = w.Write([]byte(val))
		return e
	}
	var ct strings.Builder
	ct.WriteString(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>`)
	for i := range sheets {
		fmt.Fprintf(&ct, `<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`, i+1)
	}
	ct.WriteString(`</Types>`)
	if e = write("[Content_Types].xml", ct.String()); e != nil {
		return e
	}
	write("_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`)
	var wb, rels strings.Builder
	wb.WriteString(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>`)
	rels.WriteString(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`)
	for i, s := range sheets {
		name := s.Name
		if len([]rune(name)) > 31 {
			name = string([]rune(name)[:31])
		}
		fmt.Fprintf(&wb, `<sheet name="%s" sheetId="%d" r:id="rId%d"/>`, xlsxEscape(name), i+1, i+1)
		fmt.Fprintf(&rels, `<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>`, i+1, i+1)
		var b strings.Builder
		b.WriteString(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="1" width="34" customWidth="1"/><col min="2" max="30" width="18" customWidth="1"/></cols><sheetData>`)
		for rowIndex, row := range s.Rows {
			fmt.Fprintf(&b, `<row r="%d">`, rowIndex+1)
			for colIndex, val := range row {
				addr := excelCol(colIndex+1) + strconv.Itoa(rowIndex+1)
				if val == "" {
					continue
				}
				// Preserve IDs and leading zero strings, but make quantitative cells numeric in Excel.
				if rowIndex > 0 {
					if number, err := strconv.ParseFloat(val, 64); err == nil && !math.IsNaN(number) && !math.IsInf(number, 0) && !(len(val) > 1 && val[0] == '0' && val[1] >= '0' && val[1] <= '9') {
						fmt.Fprintf(&b, `<c r="%s"><v>%s</v></c>`, addr, strconv.FormatFloat(number, 'g', -1, 64))
						continue
					}
				}
				style := ""
				if rowIndex == 0 {
					style = ` s="1"`
				}
				fmt.Fprintf(&b, `<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>`, addr, style, xlsxEscape(safeCell(val)))
			}
			b.WriteString(`</row>`)
		}
		b.WriteString(`</sheetData></worksheet>`)
		if e = write(fmt.Sprintf("xl/worksheets/sheet%d.xml", i+1), b.String()); e != nil {
			return e
		}
	}
	wb.WriteString(`</sheets></workbook>`)
	rels.WriteString(`</Relationships>`)
	if e = write("xl/workbook.xml", wb.String()); e != nil {
		return e
	}
	if e = write("xl/_rels/workbook.xml.rels", rels.String()); e != nil {
		return e
	}
	if e = write("xl/styles.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1B526F"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFill="1" applyFont="1"/></cellXfs></styleSheet>`); e != nil {
		return e
	}
	return z.Close()
}
func dcrSummaryRows(items []dcrPulse, time float64) [][]string {
	rows := [][]string{{"Source_File", "Cycle", "SOC_Block", "SOC_Percent", "Reference_V0_V", "Direction", "Current_A", "Repeat", "StepNo", fmt.Sprintf("DCIR_%.2fs_mOhm", time), "Review"}}
	keyRepeat := map[string]int{}
	for _, p := range items {
		if !p.Include {
			continue
		}
		key := fmt.Sprintf("%s/%s/%d/%s/%s/%.2f", p.Source, p.Cycle, p.Block, p.SOC, p.Direction, p.CurrentA)
		keyRepeat[key]++
		value := ""
		for _, v := range p.Values {
			if mathAbs(v.Time-time) < .0001 && v.Available {
				value = ff(v.Resistance)
				break
			}
		}
		rows = append(rows, []string{safeCell(p.Source), safeCell(p.Cycle), strconv.Itoa(p.Block), safeCell(p.SOC), ff(p.V0), p.Direction, ff(p.CurrentA), strconv.Itoa(keyRepeat[key]), safeCell(p.Step), value, safeCell(p.Review)})
	}
	return rows
}
func mathAbs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
func dcrRawRows(items []dcrPulse) [][]string {
	rows := [][]string{{"Source_File", "Cycle", "SOC_Block", "SOC_Percent", "StepNo", "Included", "Direction", "Control", "Current_A", "V0_V", "V0Source", "Duration_s", "PulseCapacity_mAh", "ReturnCapacity_mAh", "ReturnBalanceError_Percent", "Time_s", "Voltage_V", "InterpolatedCurrent_A", "DCIR_mOhm", "Review"}}
	for _, p := range items {
		if !p.Include {
			continue
		}
		for _, v := range p.Values {
			val := ""
			if v.Available {
				val = ff(v.Resistance)
			}
			rows = append(rows, []string{safeCell(p.Source), safeCell(p.Cycle), strconv.Itoa(p.Block), safeCell(p.SOC), safeCell(p.Step), strconv.FormatBool(p.Include), p.Direction, p.Control, ff(p.CurrentA), ff(p.V0), p.V0Source, ff(p.Duration), ff(p.CapacityMAh), ff(p.ReturnCapacityMAh), ff(p.BalanceError), ff(v.Time), ff(v.Voltage), ff(v.Current), val, safeCell(p.Review)})
		}
	}
	return rows
}
