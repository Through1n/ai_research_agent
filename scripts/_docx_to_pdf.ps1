# Word COM export: DOCX -> PDF (highest fidelity: TOC field, page numbers, images)
# Usage: powershell -File scripts/_docx_to_pdf.ps1 -Docx <abs path> -Pdf <abs path>
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads .ps1 as ANSI
# unless it has a UTF-8 BOM, and non-ASCII comments in a BOM-less file cause
# parser errors (the same class of bug as the Chinese .bat files).
param(
    [Parameter(Mandatory = $true)][string]$Docx,
    [Parameter(Mandatory = $true)][string]$Pdf
)

$ErrorActionPreference = 'Stop'
# Force UTF-8 console output so non-ASCII paths are not mojibake when the
# caller captures stdout (files are fine either way; only the log misleads).
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    Write-Output "warn: could not set UTF-8 console encoding"
}
$docxPath = (Resolve-Path $Docx).Path
$pdfPath = [System.IO.Path]::GetFullPath($Pdf)
$outDir = [System.IO.Path]::GetDirectoryName($pdfPath)
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0          # wdAlertsNone
    $doc = $word.Documents.Open($docxPath, $false, $true)   # ReadOnly

    # Refresh every field (TOC PAGEREF / PAGE numbers) so the TOC page numbers
    # match the real pagination.
    foreach ($story in $doc.StoryRanges) { $null = $story.Fields.Update() }
    if ($doc.TablesOfContents.Count -gt 0) { $doc.TablesOfContents.Item(1).Update() }
    $doc.Repaginate()

    # wdExportFormatPDF = 17
    $doc.ExportAsFixedFormat($pdfPath, 17, $false, 0, 0, 0, 0, 0, $true, $true, 0, $true, $true, $false)

    $pages = $doc.ComputeStatistics(2)   # wdStatisticPages
    Write-Output ("OK pages={0} pdf={1}" -f $pages, $pdfPath)
}
finally {
    if ($doc -ne $null) { $doc.Close($false) }
    if ($word -ne $null) { $word.Quit() }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    [GC]::Collect()
}
