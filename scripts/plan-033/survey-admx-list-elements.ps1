# Plan 033 / WI-011 - survey <list> element attributes across an ADMX set.
#
# WHY HasAttribute AND NOT PROPERTY ACCESS:
#   $node.valuePrefix returns $null when the attribute is absent and '' when it
#   is present-but-empty. Format-Table renders both as blank -- collapsing the
#   exact distinction WI-011 turns on, because the two have OPPOSITE semantics:
#     valuePrefix present (incl. empty) -> names are prefix + 1-based index
#     valuePrefix absent                -> the item is both name and data
#   So presence must be reported explicitly: ABSENT / EMPTY / NAMED:<value>.
#
# Point it at a different folder to survey a non-default ADMX set.

# WI-011 list-element survey.
# The distinction that matters is PRESENT-BUT-EMPTY vs ABSENT: they have
# opposite naming semantics, and Format-Table renders both as blank.
$admx = Get-ChildItem C:\Windows\PolicyDefinitions\*.admx

function Get-AttrState {
    param([System.Xml.XmlElement]$El, [string]$Name)
    if (-not $El.HasAttribute($Name)) { return 'ABSENT' }
    $v = $El.GetAttribute($Name)
    if ($v -eq '') { return 'EMPTY' }
    return "VALUE:$v"
}

$rows = foreach ($f in $admx) {
    Select-Xml -Path $f.FullName -XPath '//*[local-name()="list"]' | ForEach-Object {
        $el = $_.Node -as [System.Xml.XmlElement]
        [pscustomobject]@{
            File          = $f.Name
            ValuePrefix   = Get-AttrState $el 'valuePrefix'
            ExplicitValue = Get-AttrState $el 'explicitValue'
            Additive      = Get-AttrState $el 'additive'
        }
    }
}

"TOTAL list elements: $($rows.Count)"
""
"valuePrefix state (the WI-011 question):"
$rows | Group-Object ValuePrefix | Sort-Object Count -Descending |
    Format-Table @{n='State';e={$_.Name}}, Count -AutoSize | Out-String

"explicitValue state:"
$rows | Group-Object ExplicitValue | Format-Table @{n='State';e={$_.Name}}, Count -AutoSize | Out-String

"Sample of each valuePrefix state:"
$rows | Group-Object ValuePrefix | ForEach-Object {
    "  --- $($_.Name)"
    $_.Group | Select-Object -First 3 | ForEach-Object { "      $($_.File)" }
}
