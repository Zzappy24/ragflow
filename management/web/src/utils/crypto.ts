/**
 * RSA password obfuscation helper — mirrors RAGFlow's web/src/utils/index.ts:rsaPsw.
 *
 * This is NOT real cryptography: the key pair ships with the repo (public key
 * hard-coded below, private key + passphrase in conf/private.pem). Its purpose
 * is defense-in-depth against leaking plaintext passwords through reverse-proxy
 * access logs, browser history, and other incidental capture points. The only
 * real transport security is TLS.
 *
 * The admin panel and RAGFlow share the exact same keypair so a password
 * encrypted by either frontend can be decrypted by either backend.
 */
import { Base64 } from 'js-base64';
import JSEncrypt from 'jsencrypt';

const RAGFLOW_PUBLIC_KEY =
  '-----BEGIN PUBLIC KEY-----MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOOUEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVKRNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs2wIDAQAB-----END PUBLIC KEY-----';

export const rsaPsw = (password: string): string => {
  const encryptor = new JSEncrypt();
  encryptor.setPublicKey(RAGFLOW_PUBLIC_KEY);
  const encrypted = encryptor.encrypt(Base64.encode(password));
  if (!encrypted) {
    throw new Error('Failed to encrypt password');
  }
  return encrypted;
};
