# Cairn trust-anchor and offline bootstrap

Status: **Phase 1 implemented** (Plan 022, WI-005).

Cairn is the suite's cryptographic provenance instrument. This document describes
the trust-anchor format, the custody model, and the offline bootstrap procedure
for verifying evidence-pack provenance in GPO Studio's public matrix generator.

## Separation of concerns

Evidence packs have two independent gates:

- **Signature (eligibility):** a valid detached Ed25519 signature over the
  pack's `canonical_pack_hash` proves *who gathered* the pack and that the pack
  bytes are unmodified. This makes the pack *release-eligible*.
- **Verification (truth):** the classification-derivation gate in
  `gpo_studio.evidence.derive_claims` proves whether each claim is true. A
  `verified-rw` row requires both a passing `windows-side` record and a passing
  `endpoint` record for the same (capability, estate). A signed pack with no
  passing evidence produces no `verified-rw` claims.

These gates are mechanically independent. The signature check does not inspect
records, and the derivation check does not inspect the signature. Both must
pass for a pack to be used as release evidence.

## Trust-anchor format

A trust anchor is a JSON file that carries a single public key, its identifier,
and the algorithm:

```json
{
  "key_id": "cairn-operator-2026-07-25",
  "algorithm": "Ed25519",
  "public_key": "base64(Raw 32-byte Ed25519 public key)"
}
```

`key_id` is a free-form string that identifies the key. The same `key_id` must
appear in the matching sidecar signature. `algorithm` is always `Ed25519` in
Phase 1. `public_key` is the raw 32-byte public key encoded with standard
Base64.

Trust anchors are loaded by `gpo_studio.provenance.load_trust_anchor` and passed
to `verify_provenance_signature` alongside the pack hash and the detached
signature.

## Sidecar signature format

The evidence-pack JSON schema is not modified to include the signature. Instead,
each pack has a detached sidecar `.sig` file with this JSON format:

```json
{
  "key_id": "cairn-operator-2026-07-25",
  "algorithm": "Ed25519",
  "signature": "base64(Raw 64-byte Ed25519 signature)"
}
```

The signature value is the raw Ed25519 signature over the 32-byte SHA-256 of
the pack's canonical JSON bytes (`bytes.fromhex(canonical_pack_hash(pack))`).
Use `gpo_studio.provenance.serialize_signature` and `parse_signature` to read
and write sidecars.

## Custody model

The private key **never ships with the pack producer**. It is held by the
operator who controls the cairn signing identity. The public key is the only
key material that is distributed to verification tooling such as the public
matrix generator.

This separation prevents a self-attesting gate: if the pack producer also held
the verification key, it could sign its own unverified packs and claim
release-eligibility. The operator maintains the signing key in an offline or
HSM-backed store; the matrix generator only ever loads the public trust anchor.

## Offline bootstrap procedure

1. Generate an Ed25519 keypair:

   ```python
   from gpo_studio.provenance import generate_keypair

   private_key, public_key = generate_keypair()
   ```

2. Create a trust anchor file containing only the public key and metadata:

   ```python
   from pathlib import Path
   from gpo_studio.provenance import TrustAnchor, save_trust_anchor

   anchor = TrustAnchor(
       key_id="cairn-operator-2026-07-25",
       public_key=public_key,
       algorithm="Ed25519",
   )
   save_trust_anchor(anchor, Path("cairn-operator-2026-07-25.anchor.json"))
   ```

3. Distribute the trust anchor file to every verifier (CI jobs, reviewers, the
   public matrix generator). Keep the private key in the operator's signing
   environment.

4. When an evidence pack is gathered, sign its canonical hash and write the
   sidecar:

   ```python
   from gpo_studio.evidence import canonical_pack_hash, load_pack
   from gpo_studio.provenance import sign_pack, serialize_signature

   pack = load_pack(Path("pack.json"))
   pack_hash = bytes.fromhex(canonical_pack_hash(pack))
   sig = sign_pack(pack_hash, private_key, key_id="cairn-operator-2026-07-25")
   Path("pack.json.sig").write_bytes(serialize_signature(sig))
   ```

5. Verify a pack before deriving claims:

   ```python
   from gpo_studio.provenance import load_trust_anchor, parse_signature, verify_provenance_signature

   anchor = load_trust_anchor(Path("cairn-operator-2026-07-25.anchor.json"))
   sig = parse_signature(Path("pack.json.sig").read_bytes())
   assert verify_provenance_signature(pack_hash, sig, anchor)
   ```

## CLI integration

`scripts/generate_public_matrix.py` verifies sidecar signatures in `--matrix`
mode:

```bash
uv run python scripts/generate_public_matrix.py \
    --pack pack.json \
    --trust-anchor cairn-operator-2026-07-25.anchor.json \
    --signature pack.json.sig \
    --matrix
```

The CLI refuses packs without a valid signature unless `--allow-unsigned` is
given, which stamps the output `DRAFT` and must not be used as release evidence.
The signability gate (`redaction_verified && licensing_complete`) and the
signature gate remain independent checks inside the generator.

## Rotation and revocation

A new keypair generates a new `key_id`. Operators can rotate by publishing a new
trust anchor and signing future packs with the new key. The verifier loads the
current anchor or a set of anchors and selects the one matching the sidecar's
`key_id`. Revocation is a matter of removing the anchor from the verifier's
configuration; because verification is offline, no online revocation protocol is
required.
