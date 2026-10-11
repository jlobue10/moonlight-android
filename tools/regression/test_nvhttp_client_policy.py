#!/usr/bin/env python3
"""Compile the production NvHTTP.initializeHttpState against a recording OkHttp stub and check the
client policy: no redirect following on any of the three clients (the paired host is addressed
directly; a 302 must not steer box-art, applist, launch or clipboard fetches, nor offer the client
certificate to another server). --baseline reads NvHTTP from HEAD. JDK 17+, no network.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = 'app/src/main/java/com/limelight/nvstream/http/NvHTTP.java'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())


def method(signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


init = method('private void initializeHttpState(final LimelightCryptoProvider cryptoProvider)')

FILES = {
'okhttp3/ConnectionPool.java': 'package okhttp3;import java.util.concurrent.TimeUnit;public class ConnectionPool {public ConnectionPool(int a,long b,TimeUnit u){}}',
'okhttp3/OkHttpClient.java': '''package okhttp3;import java.net.Proxy;import java.util.concurrent.TimeUnit;import javax.net.ssl.HostnameVerifier;
public class OkHttpClient {
 public final boolean followRedirects,followSslRedirects;public final long connectTimeout,readTimeout;
 OkHttpClient(Builder b){followRedirects=b.followRedirects;followSslRedirects=b.followSslRedirects;connectTimeout=b.connectTimeout;readTimeout=b.readTimeout;}
 public Builder newBuilder(){return new Builder(this);}
 public static class Builder {
  // OkHttp defaults: redirects are followed unless the builder says otherwise.
  boolean followRedirects=true,followSslRedirects=true;long connectTimeout=10000,readTimeout=10000;
  public Builder(){} Builder(OkHttpClient c){followRedirects=c.followRedirects;followSslRedirects=c.followSslRedirects;connectTimeout=c.connectTimeout;readTimeout=c.readTimeout;}
  public Builder connectionPool(ConnectionPool p){return this;} public Builder hostnameVerifier(HostnameVerifier v){return this;}
  public Builder readTimeout(long t,TimeUnit u){readTimeout=t;return this;} public Builder connectTimeout(long t,TimeUnit u){connectTimeout=t;return this;}
  public Builder proxy(Proxy p){return this;} public Builder fastFallback(boolean b){return this;}
  public Builder followRedirects(boolean v){followRedirects=v;return this;} public Builder followSslRedirects(boolean v){followSslRedirects=v;return this;}
  public OkHttpClient build(){return new OkHttpClient(this);}}}''',
'com/limelight/nvstream/http/LimelightCryptoProvider.java': '''package com.limelight.nvstream.http;import java.security.PrivateKey;import java.security.cert.X509Certificate;
public interface LimelightCryptoProvider {X509Certificate getClientCertificate();PrivateKey getClientPrivateKey();}''',
'com/limelight/nvstream/http/NvHTTP.java': '''package com.limelight.nvstream.http;
import java.net.Proxy;import java.net.Socket;import java.security.Principal;import java.security.PrivateKey;
import java.security.cert.Certificate;import java.security.cert.CertificateException;import java.security.cert.X509Certificate;
import java.util.concurrent.TimeUnit;import javax.net.ssl.*;import okhttp3.*;
public class NvHTTP {
 public static final int SHORT_CONNECTION_TIMEOUT=3000,LONG_CONNECTION_TIMEOUT=5000,READ_TIMEOUT=7000;
 OkHttpClient httpClientLongConnectTimeout,httpClientLongConnectNoReadTimeout,httpClientShortConnectTimeout;
 X509TrustManager defaultTrustManager,trustManager;X509KeyManager keyManager;X509Certificate serverCert;
 private static X509TrustManager getDefaultTrustManager(){return new X509TrustManager(){public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
  public void checkClientTrusted(X509Certificate[] c,String a){}public void checkServerTrusted(X509Certificate[] c,String a){}};}
''' + init + '''
 public static void main(String[] args){int failures=0;NvHTTP n=new NvHTTP();n.initializeHttpState(null);
  OkHttpClient[] clients={n.httpClientLongConnectTimeout,n.httpClientShortConnectTimeout,n.httpClientLongConnectNoReadTimeout};
  String[] names={"long-connect","short-connect","long-connect-no-read"};
  for(int i=0;i<3;i++){boolean ok=clients[i]!=null&&!clients[i].followRedirects&&!clients[i].followSslRedirects;
   System.out.println((ok?"PASS ":"FAIL ")+names[i]+" client does not follow redirects");failures+=ok?0:1;}
  boolean timeouts=n.httpClientShortConnectTimeout.connectTimeout==SHORT_CONNECTION_TIMEOUT&&n.httpClientLongConnectTimeout.connectTimeout==LONG_CONNECTION_TIMEOUT
   &&n.httpClientLongConnectNoReadTimeout.readTimeout==0&&n.httpClientLongConnectTimeout.readTimeout==READ_TIMEOUT;
  System.out.println((timeouts?"PASS ":"FAIL ")+"derived clients keep their own timeouts");failures+=timeouts?0:1;
  System.exit(failures==0?0:1);}}''',
}

with tempfile.TemporaryDirectory(prefix='nvhttp-client-policy-') as directory:
    w = pathlib.Path(directory)
    for name, code in FILES.items():
        p = w / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code)
    subprocess.run(['java', 'com.sun.tools.javac.Main', '-d', str(w / 'classes'), *map(str, w.rglob('*.java'))], check=True)
    raise SystemExit(subprocess.run(['java', '-cp', str(w / 'classes'), 'com.limelight.nvstream.http.NvHTTP']).returncode)
