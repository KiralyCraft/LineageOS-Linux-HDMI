package dev.kiraly.hdmilos;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import java.io.EOFException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

final class BrokerClient {
    static final int STATE_ANDROID = 0;
    static final int STATE_DRAINING = 1;
    static final int STATE_LEASED = 2;
    static final int STATE_RESTORING = 3;
    static final int STATE_UNAVAILABLE = 4;
    static final int STATE_ERROR = 5;
    static final int STATE_AGENT_READY = 6;
    static final int STATE_STARTING_X = 7;
    static final int STATE_PROBING = 8;
    static final int STATE_ARMED = 9;
    static final int STATE_WAITING = 10;

    static final int FLAG_CONNECTED = 0x00000100;
    static final int FLAG_LEASE_READY = 0x00000200;
    static final int FLAG_ARMED = 0x00000400;
    static final int FLAG_REPLUG_REQUIRED = 0x00000800;
    static final int FLAG_CONTINUOUS = 0x80000000;

    private static final int MAGIC = 0x48444d49;
    private static final short VERSION = 3;
    private static final short OP_STATUS = 1;
    private static final short OP_TOGGLE = 5;
    private static final short OP_SET_MODE = 7;
    private static final int MESSAGE_SIZE = 160;
    private static final String SOCKET = "hdmi-los-broker-v1";
    private static final AtomicInteger REQUEST = new AtomicInteger(1);

    record Status(int result, int state, int remaining, int connector, int crtc, int plane,
                  int flags, int requestedWidth, int requestedHeight, int requestedRefreshMilliHz,
                  int activeWidth, int activeHeight, int activeRefreshMilliHz, String detail) {
        static Status unavailable(String detail) {
            return new Status(-6, STATE_UNAVAILABLE, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, detail);
        }
    }

    static Status status() {
        return request(OP_STATUS, 0, 0, 0);
    }

    static Status toggle() {
        return request(OP_TOGGLE, 0, 0, 0);
    }

    static Status setMode(int width, int height, int refreshMilliHz) {
        return request(OP_SET_MODE, width, height, refreshMilliHz);
    }

    private static Status request(short opcode, int width, int height, int refreshMilliHz) {
        try (LocalSocket socket = new LocalSocket(LocalSocket.SOCKET_STREAM)) {
            // Preparation, composer hand-off, and Xorg validation are all
            // bounded but can legitimately take up to roughly 30 seconds.
            socket.setSoTimeout(40000);
            socket.connect(new LocalSocketAddress(SOCKET, LocalSocketAddress.Namespace.ABSTRACT));
            ByteBuffer outgoing = ByteBuffer.allocate(MESSAGE_SIZE).order(ByteOrder.LITTLE_ENDIAN);
            outgoing.putInt(MAGIC);
            outgoing.putShort(VERSION);
            outgoing.putShort(opcode);
            outgoing.putInt(REQUEST.getAndIncrement());
            outgoing.position(44);
            outgoing.putInt(width);
            outgoing.putInt(height);
            outgoing.putInt(refreshMilliHz);
            OutputStream output = socket.getOutputStream();
            output.write(outgoing.array());
            output.flush();

            byte[] bytes = new byte[MESSAGE_SIZE];
            InputStream input = socket.getInputStream();
            int offset = 0;
            while (offset < bytes.length) {
                int count = input.read(bytes, offset, bytes.length - offset);
                if (count < 0) throw new EOFException("short broker response");
                offset += count;
            }
            ByteBuffer incoming = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
            if (incoming.getInt() != MAGIC || incoming.getShort() != VERSION) {
                return Status.unavailable("Broker protocol mismatch");
            }
            incoming.getShort(); // opcode
            incoming.getInt();   // request id
            int result = incoming.getInt();
            int state = incoming.getInt();
            int remaining = incoming.getInt();
            int connector = incoming.getInt();
            int crtc = incoming.getInt();
            int plane = incoming.getInt();
            incoming.getInt(); // lessee id
            int flags = incoming.getInt();
            int requestedWidth = incoming.getInt();
            int requestedHeight = incoming.getInt();
            int requestedRefreshMilliHz = incoming.getInt();
            int activeWidth = incoming.getInt();
            int activeHeight = incoming.getInt();
            int activeRefreshMilliHz = incoming.getInt();
            byte[] detailBytes = new byte[92];
            incoming.get(detailBytes);
            int length = 0;
            while (length < detailBytes.length && detailBytes[length] != 0) length++;
            String detail = new String(detailBytes, 0, length, StandardCharsets.UTF_8);
            return new Status(result, state, remaining, connector, crtc, plane, flags,
                    requestedWidth, requestedHeight, requestedRefreshMilliHz,
                    activeWidth, activeHeight, activeRefreshMilliHz, detail);
        } catch (Exception exception) {
            return Status.unavailable("Broker unavailable: " + exception.getMessage());
        }
    }

    private BrokerClient() {}
}
