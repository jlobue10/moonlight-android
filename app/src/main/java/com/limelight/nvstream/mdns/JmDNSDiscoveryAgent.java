package com.limelight.nvstream.mdns;

import android.content.Context;
import android.net.wifi.WifiManager;

import java.io.IOException;
import java.io.UnsupportedEncodingException;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.HashSet;

import javax.jmdns.JmmDNS;
import javax.jmdns.NetworkTopologyDiscovery;
import javax.jmdns.ServiceEvent;
import javax.jmdns.ServiceInfo;
import javax.jmdns.ServiceListener;
import javax.jmdns.impl.NetworkTopologyDiscoveryImpl;

import com.limelight.LimeLog;

public class JmDNSDiscoveryAgent extends MdnsDiscoveryAgent {
    private static final String SERVICE_TYPE = "_nvstream._tcp.local.";
    private WifiManager.MulticastLock multicastLock;
    private Thread discoveryThread;
    // Discovery-cycle state and result admission are guarded by this.
    private final HashSet<String> pendingResolution = new HashSet<>();
    private long discoveryGeneration;
    private ServiceListener discoveryListener;
    
    // The resolver factory's instance member has a static lifetime which
    // means our ref count and listener must be static also.
    private static int resolverRefCount = 0;
    private static HashSet<ServiceListener> listeners = new HashSet<>();
    private static ServiceListener nvstreamListener = new ServiceListener() {
        @Override
        public void serviceAdded(ServiceEvent event) {
            HashSet<ServiceListener> localListeners;
            
            // Copy the listener set into a new set so we can invoke
            // the callbacks without holding the listeners monitor the
            // whole time.
            synchronized (listeners) {
                localListeners = new HashSet<ServiceListener>(listeners);
            }
            
            for (ServiceListener listener : localListeners) {
                listener.serviceAdded(event);
            }
        }

        @Override
        public void serviceRemoved(ServiceEvent event) {
            HashSet<ServiceListener> localListeners;
            
            // Copy the listener set into a new set so we can invoke
            // the callbacks without holding the listeners monitor the
            // whole time.
            synchronized (listeners) {
                localListeners = new HashSet<ServiceListener>(listeners);
            }
            
            for (ServiceListener listener : localListeners) {
                listener.serviceRemoved(event);
            }
        }

        @Override
        public void serviceResolved(ServiceEvent event) {
            HashSet<ServiceListener> localListeners;
            
            // Copy the listener set into a new set so we can invoke
            // the callbacks without holding the listeners monitor the
            // whole time.
            synchronized (listeners) {
                localListeners = new HashSet<ServiceListener>(listeners);
            }
            
            for (ServiceListener listener : localListeners) {
                listener.serviceResolved(event);
            }
        }
    };

    public static class MyNetworkTopologyDiscovery extends NetworkTopologyDiscoveryImpl {
        @Override
        public boolean useInetAddress(NetworkInterface networkInterface, InetAddress interfaceAddress) {
            // This is an copy of jmDNS's implementation, except we omit the multicast check, since
            // it seems at least some devices lie about interfaces not supporting multicast when they really do.
            try {
                if (!networkInterface.isUp()) {
                    return false;
                }

                /*
                if (!networkInterface.supportsMulticast()) {
                    return false;
                }
                */

                if (networkInterface.isLoopback()) {
                    return false;
                }

                return true;
            } catch (Exception exception) {
                return false;
            }
        }
    }

    static {
        // Override jmDNS's default topology discovery class with ours
        NetworkTopologyDiscovery.Factory.setClassDelegate(new NetworkTopologyDiscovery.Factory.ClassDelegate() {
            @Override
            public NetworkTopologyDiscovery newNetworkTopologyDiscovery() {
                return new MyNetworkTopologyDiscovery();
            }
        });
    }

    private static JmmDNS referenceResolver() {
        synchronized (JmDNSDiscoveryAgent.class) {
            JmmDNS instance = JmmDNS.Factory.getInstance();
            if (++resolverRefCount == 1) {
                // This will cause the listener to be invoked for known hosts immediately.
                // JmDNS only supports one listener per service, so we have to do this here
                // with a static listener.
                instance.addServiceListener(SERVICE_TYPE, nvstreamListener);
            }
            return instance;
        }
    }

    private static void dereferenceResolver() {
        synchronized (JmDNSDiscoveryAgent.class) {
            if (--resolverRefCount == 0) {
                try {
                    JmmDNS.Factory.close();
                } catch (IOException e) {}
            }
        }
    }

    public JmDNSDiscoveryAgent(Context context, MdnsDiscoveryListener listener) {
        super(listener);

        // Create the multicast lock required to receive mDNS traffic
        WifiManager wifiMgr = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
        multicastLock = wifiMgr.createMulticastLock("Limelight mDNS");
        multicastLock.setReferenceCounted(false);
    }

    private synchronized boolean isCurrentDiscovery(long generation) {
        return discoveryListener != null && generation == discoveryGeneration;
    }

    private void handleResolvedServiceInfo(ServiceInfo info, long generation) {
        synchronized (this) {
            if (!isCurrentDiscovery(generation)) return;
            pendingResolution.remove(info.getName());
        }
        // The consumer may do a blocking server-info lookup. An admitted result
        // may finish after stop, but must not hold up discovery shutdown.
        try {
            handleServiceInfo(info);
        } catch (UnsupportedEncodingException e) {
            // Invalid DNS response
            LimeLog.info("mDNS: Invalid response for machine: "+info.getName());
            return;
        }
    }

    private void handleServiceInfo(ServiceInfo info) throws UnsupportedEncodingException {
        reportNewComputer(info.getName(), info.getPort(), info.getInet4Addresses(), info.getInet6Addresses());
    }
    
    public synchronized void startDiscovery(final int discoveryIntervalMs) {
        // Kill any existing discovery before starting a new one
        stopDiscovery();

        // Acquire the multicast lock to start receiving mDNS traffic
        multicastLock.acquire();

        final long generation = ++discoveryGeneration;
        // The static dispatcher snapshots listeners before invoking them. A
        // distinct listener per cycle keeps even a delayed snapshot tied to
        // the cycle that registered it, rather than to a restarted agent.
        discoveryListener = new ServiceListener() {
            @Override
            public void serviceAdded(ServiceEvent event) {
                JmDNSDiscoveryAgent.this.serviceAdded(event, generation);
            }

            @Override
            public void serviceRemoved(ServiceEvent event) {
                JmDNSDiscoveryAgent.this.serviceRemoved(event, generation);
            }

            @Override
            public void serviceResolved(ServiceEvent event) {
                // Resolution is handled synchronously by serviceAdded or the retry loop.
            }
        };
        
        // Add our listener to the set
        synchronized (listeners) {
            listeners.add(discoveryListener);
        }
        
        discoveryThread = new Thread() {
            @Override
            public void run() {
                // This may result in listener callbacks so we must register
                // our listener first.
                JmmDNS resolver = referenceResolver();
                
                try {
                    while (!Thread.interrupted() && isCurrentDiscovery(generation)) {
                        // Start an mDNS request
                        resolver.requestServiceInfo(SERVICE_TYPE, null, discoveryIntervalMs);
                        
                        // Run service resolution again for pending machines
                        ArrayList<String> pendingNames;
                        synchronized (JmDNSDiscoveryAgent.this) {
                            if (!isCurrentDiscovery(generation)) return;
                            pendingNames = new ArrayList<String>(pendingResolution);
                        }
                        for (String name : pendingNames) {
                            if (!isCurrentDiscovery(generation)) return;
                            LimeLog.info("mDNS: Retrying service resolution for machine: "+name);
                            ServiceInfo[] infos = resolver.getServiceInfos(SERVICE_TYPE, name, 500);
                            if (infos != null && infos.length != 0) {
                                LimeLog.info("mDNS: Resolved (retry) with "+infos.length+" service entries");
                                for (ServiceInfo svcinfo : infos) {
                                    handleResolvedServiceInfo(svcinfo, generation);
                                }
                            }
                        }
                        
                        // Wait for the next polling interval
                        try {
                            Thread.sleep(discoveryIntervalMs);
                        } catch (InterruptedException e) {
                            break;
                        }
                    }
                }
                finally {
                    // Dereference the resolver
                    dereferenceResolver();
                }
            }
        };
        discoveryThread.setName("mDNS Discovery Thread");
        discoveryThread.start();
    }
    
    public synchronized void stopDiscovery() {
        // Invalidate before interrupting: JmDNS may return a resolution after
        // the interrupt, and a dispatcher may already hold a listener snapshot.
        ++discoveryGeneration;
        // Release the multicast lock to stop receiving mDNS traffic
        multicastLock.release();

        // Remove our listener from the set
        synchronized (listeners) {
            if (discoveryListener != null) listeners.remove(discoveryListener);
        }
        discoveryListener = null;
        pendingResolution.clear();
        
        // If there's already a running thread, interrupt it
        if (discoveryThread != null) {
            discoveryThread.interrupt();
            discoveryThread = null;
        }
    }

    private void serviceAdded(ServiceEvent event, long generation) {
        if (!isCurrentDiscovery(generation)) return;
        LimeLog.info("mDNS: Machine appeared: "+event.getInfo().getName());

        ServiceInfo info = event.getDNS().getServiceInfo(SERVICE_TYPE, event.getInfo().getName(), 500);
        if (info == null) {
            // This machine is pending resolution
            synchronized (this) {
                if (isCurrentDiscovery(generation) && pendingResolution.size() < MAX_TRACKED_SERVICES) {
                    pendingResolution.add(event.getInfo().getName());
                }
            }
            return;
        }
        
        LimeLog.info("mDNS: Resolved (blocking)");
        handleResolvedServiceInfo(info, generation);
    }

    private synchronized void serviceRemoved(ServiceEvent event, long generation) {
        if (!isCurrentDiscovery(generation)) return;
        pendingResolution.remove(event.getInfo().getName());
        LimeLog.info("mDNS: Machine disappeared: "+event.getInfo().getName());
    }
}
