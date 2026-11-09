param([switch]$AsJson)

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRows = @(foreach ($folder in @('app', 'static')) {
    Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -File -Recurse |
        Where-Object { $_.Extension -in @('.py', '.js', '.html', '.css') } |
        Sort-Object FullName | ForEach-Object {
            $sourceLines = @(Get-Content -LiteralPath $_.FullName -Encoding UTF8)
            [PSCustomObject]@{
                File = $_.FullName.Substring($projectRoot.Length + 1).Replace('\', '/')
                PhysicalLines = $sourceLines.Count
                NonblankLines = @($sourceLines | Where-Object { $_.Trim().Length -gt 0 }).Count
            }
        }
})
$result = [PSCustomObject]@{
    Scope = 'app/ and static/ only; comments included; tests, tools, docs and generated output excluded'
    Files = $sourceRows
    PhysicalLines = ($sourceRows | Measure-Object PhysicalLines -Sum).Sum
    NonblankLines = ($sourceRows | Measure-Object NonblankLines -Sum).Sum
}
if ($AsJson) { $result | ConvertTo-Json -Depth 4 }
else { $sourceRows | Format-Table -AutoSize; $result | Select-Object Scope, PhysicalLines, NonblankLines | Format-List }
