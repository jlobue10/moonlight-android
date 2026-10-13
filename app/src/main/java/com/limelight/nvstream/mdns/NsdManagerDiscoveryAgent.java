package com.limelight.nvstream.mdns;

import android.annotation.TargetApi;
import android.content.Context;
import android.net.Network;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.os.Build;

import com.limelight.LimeLog;

import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.util.HashMap;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

@TargetApi(Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
public class NsdManagerDiscoveryAgent extends MdnsDiscoveryAgent {
    private static final String SERVICE_TYPE = "_nvstream._tcp";
    private final NsdManager nsdManager;
    private final Object listenerLock = new Object();
    private NsdManager.DiscoveryListener pendingListener;
    private NsdManager.DiscoveryListener activeListener;
    private final HashMap<ServiceKey, NsdManager.ServiceInfoCallback> serviceCallbacks = new HashMap<>();
    private final ThreadPoolExecutor executor = new ThreadPoolExecutor(1, 1, 30, TimeUnit.SECONDS, new LinkedBlockingQueue<>());

    // A host can appear on more than one network. Losing one route must not
    // unregister another network's identically named service.
    private static final class ServiceKey {
        private final String name;
        private final Network network;

        ServiceKey(NsdServiceInfo info) {
            name = info.getServiceName();
            network = info.getNetwork();
        }

        @Override
        public boolean equals(Object other) {
            if (!(other instanceof ServiceKey)) return false;
            ServiceKey key = (ServiceKey) other;
            return Objects.equals(name, key.name) && Objects.equals(network, key.network);
        }

        @Override
        public int hashCode() {
            return Objects.hash(name, network);
        }
    }

    private NsdManager.DiscoveryListener createDiscoveryListener() {
        return new NsdManager.DiscoveryListener() {
            @Override
            public void onStartDiscoveryFailed(String serviceType, int errorCode) {
                LimeLog.severe("NSD: Service discovery start failed: " + errorCode);

                // This listener is no longer pending after this failure
                synchronized (listenerLock) {
                    if (pendingListener != this) {
                        return;
                    }

                    pendingListener = null;
                }

                listener.notifyDiscoveryFailure(new RuntimeException("onStartDiscoveryFailed(): " + errorCode));
            }

            @Override
            public void onStopDiscoveryFailed(String serviceType, int errorCode) {
                LimeLog.severe("NSD: Service discovery stop failed: " + errorCode);

                // This listener is no longer active after this failure
                synchronized (listenerLock) {
                    if (activeListener != this) {
                        return;
                    }

                    activeListener = null;
                    unregisterServiceCallbacks();
                }
            }

            @Override
            public void onDiscoveryStarted(String serviceType) {
                LimeLog.info("NSD: Service discovery started");

                synchronized (listenerLock) {
                    if (pendingListener != this) {
                        // If we registered another discovery listener in the meantime, stop this one
                        nsdManager.stopServiceDiscovery(this);
                        return;
                    }

                    pendingListener = null;
                    activeListener = this;
                }
            }

            @Override
            public void onDiscoveryStopped(String serviceType) {
                LimeLog.info("NSD: Service discovery stopped");

                synchronized (listenerLock) {
                    if (activeListener != this) {
                        return;
                    }

                    activeListener = null;
                    unregisterServiceCallbacks();
                }
            }

            @Override
            public void onServiceFound(NsdServiceInfo nsdServiceInfo) {
                // Protect against racing stopDiscovery() call
                synchronized (listenerLock) {
                    // Ignore callbacks if we're not the active listener
                    if (activeListener != this) {
                        return;
                    }

                    final String serviceName = nsdServiceInfo.getServiceName();
                    final ServiceKey serviceKey = new ServiceKey(nsdServiceInfo);
                    // Repeated found events must not replace the only handle capable of
                    // unregistering an existing callback. Bound the platform registrations,
                    // not just the resolved-computer set in the base class.
                    if (serviceCallbacks.containsKey(serviceKey) || serviceCallbacks.size() >= MAX_TRACKED_SERVICES) {
                        return;
                    }
                    final NsdManager.DiscoveryListener owner = this;
                    LimeLog.info("NSD: Machine appeared: " + serviceName);

                    NsdManager.ServiceInfoCallback serviceInfoCallback = new NsdManager.ServiceInfoCallback() {
                        @Override
                        public void onServiceInfoCallbackRegistrationFailed(int errorCode) {
                            synchronized (listenerLock) {
                                if (activeListener != owner || serviceCallbacks.get(serviceKey) != this) {
                                    return;
                                }
                                // Failed registrations are already removed by NsdManager.
                                serviceCallbacks.remove(serviceKey);
                                LimeLog.severe("NSD: Service info callback registration failed: " + errorCode);
                                listener.notifyDiscoveryFailure(new RuntimeException("onServiceInfoCallbackRegistrationFailed(): " + errorCode));
                            }
                        }

                        @Override
                        public void onServiceUpdated(NsdServiceInfo nsdServiceInfo) {
                            synchronized (listenerLock) {
                                // Unregister does not retract tasks already queued on the
                                // executor. Both the discovery and service must still own us.
                                if (activeListener != owner || serviceCallbacks.get(serviceKey) != this) {
                                    return;
                                }
                            }
                            // The consumer performs blocking server-info requests. Do not
                            // hold the lifecycle lock while an admitted callback runs: stop
                            // must remain responsive on the UI thread.
                            LimeLog.info("NSD: Machine resolved: " + nsdServiceInfo.getServiceName());
                            reportNewComputer(nsdServiceInfo.getServiceName(), nsdServiceInfo.getPort(),
                                    getV4Addrs(nsdServiceInfo.getHostAddresses()),
                                    getV6Addrs(nsdServiceInfo.getHostAddresses()));
                        }

                        @Override
                        public void onServiceLost() {
                        }

                        @Override
                        public void onServiceInfoCallbackUnregistered() {
                            synchronized (listenerLock) {
                                if (serviceCallbacks.get(serviceKey) == this) {
                                    serviceCallbacks.remove(serviceKey);
                                }
                            }
                        }
                    };

                    serviceCallbacks.put(serviceKey, serviceInfoCallback);
                    try {
                        nsdManager.registerServiceInfoCallback(nsdServiceInfo, executor, serviceInfoCallback);
                    } catch (IllegalArgumentException | IllegalStateException e) {
                        serviceCallbacks.remove(serviceKey);
                        listener.notifyDiscoveryFailure(e);
                    }
                }
            }

            @Override
            public void onServiceLost(NsdServiceInfo nsdServiceInfo) {
                // Protect against racing stopDiscovery() call
                synchronized (listenerLock) {
                    // Ignore callbacks if we're not the active listener
                    if (activeListener != this) {
                        return;
                    }

                    LimeLog.info("NSD: Machine lost: " + nsdServiceInfo.getServiceName());

                    NsdManager.ServiceInfoCallback serviceInfoCallback = serviceCallbacks.remove(new ServiceKey(nsdServiceInfo));
                    if (serviceInfoCallback != null) {
                        unregisterServiceCallback(serviceInfoCallback);
                    }
                }
            }
        };
    }

    public NsdManagerDiscoveryAgent(Context context, MdnsDiscoveryListener listener) {
        super(listener);
        this.nsdManager = context.getSystemService(NsdManager.class);
        // The agent can be stopped and reused, so leave the executor available while
        // allowing its sole worker to retire after discovery becomes idle.
        executor.allowCoreThreadTimeOut(true);
    }

    @Override
    public void startDiscovery(int discoveryIntervalMs) {
        synchronized (listenerLock) {
            // Register a new service discovery listener if there's not already one starting or running
            if (pendingListener == null && activeListener == null) {
                pendingListener = createDiscoveryListener();
                nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, pendingListener);
            }
        }
    }

    @Override
    public void stopDiscovery() {
        // Protect against racing ServiceInfoCallback and DiscoveryListener callbacks
        synchronized (listenerLock) {
            // Clear any pending listener to ensure the discoverStarted() callback
            // will realize it's gone and stop itself.
            pendingListener = null;

            // Unregister the service discovery listener
            if (activeListener != null) {
                NsdManager.DiscoveryListener stoppedListener = activeListener;
                activeListener = null;
                try {
                    nsdManager.stopServiceDiscovery(stoppedListener);
                } catch (IllegalArgumentException e) {
                    // The platform may have already removed this listener. Still release
                    // the separately registered service callbacks below.
                    LimeLog.warning("NSD: Discovery already stopped: " + e);
                }
            }

            unregisterServiceCallbacks();
        }
    }

    // Called with listenerLock held. Retire ownership before asynchronous unregistration.
    private void unregisterServiceCallbacks() {
        NsdManager.ServiceInfoCallback[] callbacks = serviceCallbacks.values().toArray(new NsdManager.ServiceInfoCallback[0]);
        serviceCallbacks.clear();
        for (NsdManager.ServiceInfoCallback callback : callbacks) {
            unregisterServiceCallback(callback);
        }
    }

    private void unregisterServiceCallback(NsdManager.ServiceInfoCallback callback) {
        try {
            nsdManager.unregisterServiceInfoCallback(callback);
        } catch (IllegalArgumentException e) {
            // A queued registration-failure callback may not have reached our executor
            // yet, even though the platform has already removed the registration.
            LimeLog.warning("NSD: Service callback already unregistered: " + e);
        }
    }

    private static Inet4Address[] getV4Addrs(List<InetAddress> addrs) {
        int matchCount = 0;
        for (InetAddress addr : addrs) {
            if (addr instanceof Inet4Address) {
                matchCount++;
            }
        }

        Inet4Address[] matching = new Inet4Address[matchCount];

        int i = 0;
        for (InetAddress addr : addrs) {
            if (addr instanceof Inet4Address) {
                matching[i++] = (Inet4Address) addr;
            }
        }

        return matching;
    }

    private static Inet6Address[] getV6Addrs(List<InetAddress> addrs) {
        int matchCount = 0;
        for (InetAddress addr : addrs) {
            if (addr instanceof Inet6Address) {
                matchCount++;
            }
        }

        Inet6Address[] matching = new Inet6Address[matchCount];

        int i = 0;
        for (InetAddress addr : addrs) {
            if (addr instanceof Inet6Address) {
                matching[i++] = (Inet6Address) addr;
            }
        }

        return matching;
    }
}
