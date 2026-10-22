# Predome public clinical snapshot — independent timestamp

The clinical-records.zip file contains actual public records and readable reports for 12 formal trial questions (48 assigned model records) and 8 historical replays.

FreeTSA signed its SHA-256 message imprint at **2026-10-22T18:48:48+00:00**. OpenSSL verifies both the ZIP bytes and the original query nonce. Only a digest and nonce were sent to the authority.

This proves the public ZIP existed by that signature time. It does not prove the earlier original model-generation times, clinical accuracy, patent benefit, Agent superiority, or human scientific review. Existing registration/run proofs remain separate. Historical replays are not prospective wins. The ZIP's own export manifest was written before this detached timestamp and therefore correctly states that it contains no independent attestation of its own.

Public ZIP SHA-256: `1a0144906b743e03c50b13b2c1e9554ec488381b88e4939d9d8ec53fa116825c`

## Verify offline

Obtain a trusted copy of the verifier from the EmerisBiotech repository, scripts/public_snapshot_proof.py. In apps/predome run:

```sh
python scripts/public_snapshot_proof.py verify --directory /path/to/extracted-kit
```

For the underlying cryptography:

```sh
openssl ts -verify -data clinical-records.zip -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -verify -queryfile request.tsq -in timestamp.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -reply -in timestamp.tsr -text
```

The supplied CA alone is not an independent identity check. Compare the certificates with the pinned hashes in the trusted repository or the authority's HTTPS files at https://freetsa.org/files/cacert.pem and https://freetsa.org/files/tsa.crt.

CA SHA-256: `2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438`

TSA certificate SHA-256: `8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467`

No model call, ledger change, paid prediction, patient data upload, or new clinical adjudication is performed by this verification kit. This is a cryptographic research record, not legal notarization.
