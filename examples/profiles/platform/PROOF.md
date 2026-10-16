<!-- Output of prove.py, run with outbound network denied (a sandbox profile that denies network access; on Linux use unshare -rn). Regenerate with: unshare -rn python3 examples/profiles/platform/prove.py --out DIR --scratch DIR -->
# Platform checks: proof against stand-in tools

Every case is a real `seaworthy run --only CHECK` (not a dry run) with fake `xcrun`, `security`, `spctl`, `gh` and `ssh` on PATH (`shims/shim`); no vendor endpoint was contacted. Certificates are made-up. A case with no tool on PATH shows how the check degrades. Each case also asserts that the tool only received read-only calls and that a secret sentinel set in the environment never reached an argument list or the report.

| check | case | status | exit | evidence |
| --- | --- | --- | --- | --- |
| notary-credentials | credential accepted by Apple | pass | 0 | Apple accepted the credential; 2 previous submission(s) listed |
| notary-credentials | keychain profile does not exist | fail | 3 | the keychain profile does not exist on this machine |
| notary-credentials | credential reaches the wrong team | fail | 3 | Apple refused: the team is invalid or not accessible with this credential |
| notary-credentials | Apple answers 401: says the three meanings and the next step | fail | 3 | Apple answered HTTP 401: the credential was not accepted (invalid credential, pending agreement, or transient) |
| notary-credentials | a 401 still fails at a stage where the credential applies | fail | 3 | Apple answered HTTP 401: the credential was not accepted (invalid credential, pending agreement, or transient) |
| notary-credentials | stage publish: not applicable, existing ticket and Gatekeeper shown | skip | 0 | not applicable at stage 'publish': notarization already happened, so the credential is not what this stage needs; existing ticket: dist/Example-1.4.0.dmg: ticket stapled, Gatekeeper accepted |
| notary-credentials | stage publish: the existing artifact has no ticket | warn | 4 | not applicable at stage 'publish': notarization already happened, so the credential is not what this stage needs; but the existing artifacts are not clean: dist/Example-1.4.0.dmg: no valid stapled ticket (The file does not have a ticket stapled to it.) |
| notary-credentials | stage publish: Gatekeeper rejects the artifact | warn | 4 | not applicable at stage 'publish': notarization already happened, so the credential is not what this stage needs; but the existing artifacts are not clean: dist/Example-1.4.0.dmg: ticket stapled, but Gatekeeper does not accept it (rejected) |
| notary-credentials | agreement refusal hides the credential | unknown | 3 | Apple answered with an agreement refusal before the credential could be confirmed |
| notary-credentials | notarytool prints something unexpected | unknown | 3 | notarytool printed something that is not JSON: this is not json |
| notary-credentials | no xcrun on this machine | unknown | 3 | cannot check here: xcrun is not installed (notarization is checked on a Mac with the Xcode command line tools) |
| notary-credentials | Apple ID variables instead of a keychain profile (good) | pass | 0 | Apple accepted the credential; 2 previous submission(s) listed |
| notary-credentials | Apple ID variables not set: skipped with the reason, not a failure | skip | 0 | not checked: no keychain_profile was given and APPLE_ID, APPLE_TEAM_ID, APPLE_APP_SPECIFIC_PASSWORD are not set in this environment; nothing was sent to Apple |
| notary-agreement | no refusal and a fresh human confirmation | pass | 0 | no agreement refusal from Apple; the account holder confirmed no pending agreement on 2026-10-12 |
| notary-agreement | Apple says the agreement is missing or expired | fail | 3 | a required Apple agreement is missing or has expired; the Account Holder must accept it, and a retry will not help |
| notary-agreement | history works but nobody confirmed | unknown | 3 | the credential works, but nothing proves there is no pending agreement: no dated human confirmation was given |
| notary-agreement | confirmation is 90 days old | unknown | 3 | the last human agreement confirmation (the account holder, 2026-07-17) is 90 days old; the limit is 30 |
| notary-agreement | no xcrun on this machine | unknown | 3 | cannot check here: xcrun is not installed (notarization is checked on a Mac with the Xcode command line tools) |
| developer-id-identity | valid identity, G2 issuer, 1000 days left | pass | 0 | identity present and valid, expires 2029-07-11 (999 days), issuer G2 |
| developer-id-identity | identity not in the keychain | fail | 3 | the identity 'Developer ID Application: Example Corp (TEST000000)' is not in the keychain |
| developer-id-identity | keychain flags the identity as expired | fail | 3 | the identity is in the keychain but not valid: CSSMERR_TP_CERT_EXPIRED |
| developer-id-identity | certificate expired in 2021 | fail | 3 | the certificate expired on 2021-01-01 |
| developer-id-identity | certificate expires in 10 days | fail | 3 | the certificate expires in 9 days (2026-10-25), under the 30-day floor |
| developer-id-identity | issuer is the original authority, not G2 | fail | 3 | the issuer is not the Developer ID G2 authority; the original authority expires on 2027-02-01 and the Account Holder must reissue the certificate |
| developer-id-identity | no security tool on this machine | unknown | 3 | cannot check here: the macOS 'security' tool is not installed (the keychain is read on the Mac that signs) |
| notarization-configured | one complete set and hardened runtime on | pass | 0 | credential set 'apple-id' is complete and hardened runtime is on |
| notarization-configured | no credential in the environment | fail | 3 | no complete notarization credential set is present |
| notarization-configured | a set with a variable missing | fail | 3 | no complete notarization credential set is present; apple-id is missing APPLE_TEAM_ID |
| notarization-configured | two complete sets | fail | 3 | more than one complete credential set is present (apple-id, keychain-profile); the builder will pick one and which is not obvious |
| notarization-configured | hardenedRuntime is false | fail | 3 | hardenedRuntime is false in electron-builder.yml |
| notarization-configured | the config does not say | unknown | 3 | electron-builder.yml does not say hardenedRuntime |
| windows-signing-role | token issued and a signer role assigned | pass | 0 | token issued and the identity holds 'Artifact Signing Certificate Profile Signer' at the certificate profile scope |
| windows-signing-role | token issued but no signer role | fail | 3 | token issued, but the identity holds no signer role at the certificate profile scope ('Artifact Signing Certificate Profile Signer' or 'Trusted Signing Certificate Profile Signer') |
| windows-signing-role | not signed in to Azure | fail | 3 | the Azure CLI is not signed in; no token can be issued |
| windows-signing-role | no permission to read role assignments | unknown | 3 | a token was issued, but this identity may not read role assignments at that scope, so the signer role is unproven |
| windows-signing-role | az is not installed on the host | unknown | 3 | cannot check here: az is not installed on host 'signer' |
| windows-signing-role | the host cannot be reached | unknown | 3 | cannot check here: the host 'signer' cannot be reached |
| windows-signing-role | no ssh on this machine | unknown | 3 | cannot check here: ssh is not installed (it is needed to run commands on host 'signer') |
| windows-signing-setup | module visible, signtool new enough, endpoint and timestamp right | pass | 0 | signing setup is in place (4 part(s) checked) |
| windows-signing-setup | module not visible to PowerShell 7 | fail | 3 | the TrustedSigning module is not visible to pwsh |
| windows-signing-setup | the module is installed twice | fail | 3 | TrustedSigning is installed as 0.5.8,0.5.0, expected exactly one copy at 0.5.8 |
| windows-signing-setup | signtool older than the floor | fail | 3 | signtool 10.0.1000.5 is older than 10.0.2261.755 |
| windows-signing-setup | signtool missing at the path | fail | 3 | signtool is not at C:\signing\signtool.exe |
| windows-signing-setup | metadata endpoint is another region | fail | 3 | the metadata endpoint is https://weu.codesigning.azure.net, expected https://eus.codesigning.azure.net (a region mismatch commonly causes a 403) |
| windows-signing-setup | the signing script names no timestamp server | fail | 3 | C:\signing\sign.ps1 does not name the timestamp server http://timestamp.acs.microsoft.com |
| windows-signing-setup | the host cannot be reached | unknown | 3 | cannot check here: the host 'signer' cannot be reached |
| windows-signing-setup | pwsh path with spaces over a cmd.exe ssh shell is quoted | pass | 0 | signing setup is in place (4 part(s) checked) |
| windows-signing-setup | pwsh path with spaces over a PowerShell ssh shell is called with & | pass | 0 | signing setup is in place (4 part(s) checked) |
| windows-signing-setup | another tool's login-profile noise on a good run is ignored | pass | 0 | signing setup is in place (4 part(s) checked) |
| windows-signing-setup | noise that names another tool is not 'pwsh is not installed' | unknown | 3 | could not ask for the module: fnm : The term 'fnm' is not recognized as the name of a cmdlet, function, script file, or operable program.; could not read the signtool version; the signing metadata file is not JSON; could not read C:\signing\sign.ps1: fnm : The term 'fnm' is not recognized as the na... |
| secrets-present | both named secrets exist | pass | 0 | all 2 named secret(s) exist in example-org/example-app |
| secrets-present | one named secret is missing | fail | 3 | GH_TOKEN not in the secrets of example-org/example-app |
| secrets-present | the token may not list secrets | unknown | 3 | the secret list could not be read: HTTP 403: Resource not accessible by personal access token |
| secrets-present | no gh on this machine | unknown | 3 | cannot check here: gh is not installed |
| draft-release-state | a draft with the right assets, count and body | pass | 0 | draft v1.4.0 (id 4242) is in place with 3 asset(s) |
| draft-release-state | the draft id matches the pinned id | pass | 0 | draft v1.4.0 (id 4242) is in place with 3 asset(s) |
| draft-release-state | no release for the tag | fail | 3 | no release (draft or published) exists for v1.4.0 in example-org/example-releases |
| draft-release-state | the release is already published | fail | 3 | v1.4.0 is already published; a published release cannot be repaired in place |
| draft-release-state | the draft id is not the pinned one | fail | 3 | the draft's id is 4242, not the pinned 7 |
| draft-release-state | an asset is missing | fail | 3 | missing asset(s): app-linux.AppImage; 2 asset(s), expected exactly 3 |
| draft-release-state | an asset is half uploaded | fail | 3 | asset(s) not fully uploaded: app-mac.dmg, app-win.exe, app-linux.AppImage |
| draft-release-state | the body differs from the notes file | fail | 3 | the draft body is not identical to RELEASE_NOTES_{X_Y_Z}.md |
| draft-release-state | immutable releases on (good) | pass | 0 | draft v1.4.0 (id 4242) is in place with 3 asset(s) |
| draft-release-state | immutable releases off | fail | 3 | immutable releases are not enabled for example-org/example-releases |
| draft-release-state | immutable setting unreadable | unknown | 3 | the immutable-releases setting could not be read (gh api repos/example-org/example-releases/immutable-releases failed: gh: Not Found (HTTP 404)) |
| draft-release-state | no gh on this machine | unknown | 3 | cannot check here: gh is not installed |

Cases: 66, unexpected: 0. Every check was seen to pass and to fail.
Read-only calls received by the tools across all cases: 100.
