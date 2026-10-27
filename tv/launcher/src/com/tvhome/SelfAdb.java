package com.tvhome;

import android.content.Context;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;

import javax.crypto.Cipher;

/**
 * Minimal ADB client for this TV's own adbd on localhost, so the launcher can
 * re-run the bloat cleanup after every boot. The first connection shows the
 * "Allow USB debugging?" prompt for this app's key.
 */
final class SelfAdb implements AutoCloseable {
    private static final int CNXN = 0x4e584e43, AUTH = 0x48545541, OPEN = 0x4e45504f,
            OKAY = 0x59414b4f, CLSE = 0x45534c43, WRTE = 0x45545257;
    private static final int AUTH_TOKEN = 1, AUTH_SIGNATURE = 2, AUTH_RSAPUBLICKEY = 3;
    private static final byte[] SHA1_DIGEST_INFO = {0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b,
            0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14};

    private final Socket socket;
    private final DataInputStream in;
    private final OutputStream out;
    private int nextLocalId = 1;

    private SelfAdb(Socket socket) throws IOException {
        this.socket = socket;
        this.in = new DataInputStream(socket.getInputStream());
        this.out = socket.getOutputStream();
    }

    static SelfAdb connect(Context context, int timeoutMs) throws Exception {
        KeyPair keys = loadOrCreateKeys(context);
        Socket s = new Socket("127.0.0.1", 5555);
        s.setSoTimeout(timeoutMs);
        SelfAdb adb = new SelfAdb(s);
        adb.send(CNXN, 0x01000001, 256 * 1024, "host::\0".getBytes(StandardCharsets.UTF_8));
        boolean signed = false;
        while (true) {
            Msg m = adb.read();
            if (m.cmd == CNXN) return adb;
            if (m.cmd != AUTH || m.arg0 != AUTH_TOKEN) continue;
            if (!signed) {
                Cipher c = Cipher.getInstance("RSA/ECB/PKCS1Padding");
                c.init(Cipher.ENCRYPT_MODE, keys.getPrivate());
                c.update(SHA1_DIGEST_INFO);
                adb.send(AUTH, AUTH_SIGNATURE, 0, c.doFinal(m.data));
                signed = true;
            } else {
                byte[] pub = (encodePublicKey((RSAPublicKey) keys.getPublic()) + " tvhome@tv\0")
                        .getBytes(StandardCharsets.UTF_8);
                adb.send(AUTH, AUTH_RSAPUBLICKEY, 0, pub);
            }
        }
    }

    /** Runs a shell command and returns its output. */
    String shell(String command) throws IOException {
        int localId = nextLocalId++;
        send(OPEN, localId, 0, ("shell:" + command + "\0").getBytes(StandardCharsets.UTF_8));
        ByteArrayOutputStream result = new ByteArrayOutputStream();
        int remoteId = 0;
        while (true) {
            Msg m = read();
            if (m.arg1 != localId) continue;
            if (m.cmd == OKAY) {
                remoteId = m.arg0;
            } else if (m.cmd == WRTE) {
                result.write(m.data);
                send(OKAY, localId, m.arg0, new byte[0]);
            } else if (m.cmd == CLSE) {
                send(CLSE, localId, remoteId, new byte[0]);
                return result.toString(StandardCharsets.UTF_8.name());
            }
        }
    }

    @Override
    public void close() throws IOException {
        socket.close();
    }

    private static final class Msg {
        int cmd, arg0, arg1;
        byte[] data;
    }

    private void send(int cmd, int arg0, int arg1, byte[] data) throws IOException {
        int sum = 0;
        for (byte b : data) sum += b & 0xff;
        ByteBuffer h = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN);
        h.putInt(cmd).putInt(arg0).putInt(arg1).putInt(data.length).putInt(sum).putInt(~cmd);
        out.write(h.array());
        out.write(data);
        out.flush();
    }

    private Msg read() throws IOException {
        byte[] header = new byte[24];
        in.readFully(header);
        ByteBuffer h = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN);
        Msg m = new Msg();
        m.cmd = h.getInt();
        m.arg0 = h.getInt();
        m.arg1 = h.getInt();
        m.data = new byte[h.getInt()];
        in.readFully(m.data);
        return m;
    }

    private static KeyPair loadOrCreateKeys(Context context) throws Exception {
        File priv = new File(context.getFilesDir(), "adbkey");
        File pub = new File(context.getFilesDir(), "adbkey.pub");
        KeyFactory kf = KeyFactory.getInstance("RSA");
        if (priv.exists() && pub.exists()) {
            return new KeyPair(
                    kf.generatePublic(new X509EncodedKeySpec(Files.readAllBytes(pub.toPath()))),
                    kf.generatePrivate(new PKCS8EncodedKeySpec(Files.readAllBytes(priv.toPath()))));
        }
        KeyPairGenerator gen = KeyPairGenerator.getInstance("RSA");
        gen.initialize(2048);
        KeyPair kp = gen.generateKeyPair();
        Files.write(priv.toPath(), kp.getPrivate().getEncoded());
        Files.write(pub.toPath(), kp.getPublic().getEncoded());
        return kp;
    }

    /** Android's adb public key format: little-endian modulus words plus Montgomery constants. */
    private static String encodePublicKey(RSAPublicKey key) {
        int words = 64;
        BigInteger r32 = BigInteger.ONE.shiftLeft(32);
        BigInteger n = key.getModulus();
        BigInteger rr = BigInteger.ONE.shiftLeft(words * 32 * 2).mod(n);
        BigInteger n0inv = n.mod(r32).modInverse(r32).negate().mod(r32);
        ByteBuffer b = ByteBuffer.allocate(4 + 4 + words * 4 * 2 + 4).order(ByteOrder.LITTLE_ENDIAN);
        b.putInt(words).putInt(n0inv.intValue());
        for (int i = 0; i < words; i++) b.putInt(n.shiftRight(i * 32).mod(r32).intValue());
        for (int i = 0; i < words; i++) b.putInt(rr.shiftRight(i * 32).mod(r32).intValue());
        b.putInt(key.getPublicExponent().intValue());
        return Base64.encodeToString(b.array(), Base64.NO_WRAP);
    }
}
