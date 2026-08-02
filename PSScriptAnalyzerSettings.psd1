# PSScriptAnalyzer configuration for the Plan 033 oracle harness scripts.
#
# These scripts are how evidence is produced: they run on Windows hosts and
# guests, and a defect in one of them corrupts a certification rather than
# merely failing a build. They were unlinted until 2026-08-02 while
# windows-evidence-lab parsed and analysed every one of its own -- the
# asymmetry was backwards, since these are the scripts whose output is cited.
#
# Every exclusion below names a deliberate pattern in this repository. An
# exclusion is a claim that the rule's assumption does not hold here; if you
# add one, say which assumption and why.
@{
    Severity = @('Error', 'Warning')

    ExcludeRules = @(
        # False positive on the remoting idiom this harness is built from.
        # These scripts pass state into remote scope with param() +
        # -ArgumentList, which is the correct pattern for a script block that
        # must also be callable locally; $using: would be WRONG there and does
        # not work with -ArgumentList at all.
        'PSUseUsingScopeModifierInNewRunspaces',

        # The acb boundary. Credentials arrive as environment variables from a
        # brokered checkout and must become a PSCredential to be used at all;
        # ConvertTo-SecureString -AsPlainText is the only conversion available,
        # and a PSCredential cannot be deserialized across machines. The secret
        # never touches disk, argv, or output. Same justification the evidence
        # lab records for its identical boundary.
        'PSAvoidUsingConvertToSecureStringWithPlainText',
        'PSAvoidUsingPlainTextForPassword',

        # -CheckOnly is this family's -WhatIf, and the harness scripts are
        # invoked non-interactively by a scheduled task where a confirmation
        # prompt would hang the lane rather than protect anything.
        'PSUseShouldProcessForStateChangingFunctions',

        # Deliberate: `(($a, $b) -ne $null) -join '; '` is array filtering, not
        # a scalar null comparison -- it drops the empty members before
        # joining error strings. The rule assumes a scalar operand. Rewriting
        # it as $null -eq ... would invert the meaning and silently produce
        # ';'-prefixed error text.
        'PSPossibleIncorrectComparisonWithNull',

        # Write-Host in Write-HarnessLog is deliberate and load-bearing: the
        # harness writes its result as JSON on the success stream, and the
        # finalizers parse it. A logger on Write-Output would interleave
        # progress text into the document being parsed. Write-Host is the one
        # sink that reaches an operator watching without entering that stream.
        'PSAvoidUsingWriteHost',

        # Function names describing collections read better plural here
        # (Get-ResidualTasks and friends) and these are internal helpers, not a
        # published module surface.
        'PSUseSingularNouns'
    )
}
