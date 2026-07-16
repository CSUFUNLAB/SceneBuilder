#ifndef NETWORK_SCENE_HELPER_H
#define NETWORK_SCENE_HELPER_H

#include "ns3/application-container.h"
#include "ns3/flow-monitor.h"
#include "ns3/ipv4-header.h"
#include "ns3/ipv4-l3-protocol.h"
#include "ns3/network-scene-reader.h"
#include "ns3/net-device-container.h"
#include "ns3/node-container.h"
#include "ns3/nstime.h"
#include "ns3/packet.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace ns3
{

class FlowMonitorHelper;
class Ipv4;
class QueueDisc;
class QueueDiscItem;

class NetworkSceneHelper
{
  public:
    NetworkSceneHelper();
    ~NetworkSceneHelper();

    void SetSceneDirectory(const std::string& sceneDirectory);
    void SetApplicationStartTime(Time startTime);
    void SetApplicationStopTime(Time stopTime);
    void SetDefaultChannelDelay(Time delay);
    void SetPacketSize(uint32_t packetSize);
    void SetValueScaleFactor(double scaleFactor);
    void SetEventFile(const std::string& eventFile);
    void SetResultPath(const std::string& resultPath);

    void Install();
    void WriteResults() const;

    NodeContainer GetNodes() const;
    ApplicationContainer GetApplications() const;
    uint32_t GetNodeCount() const;
    uint32_t GetChannelCount() const;
    uint32_t GetFlowCount() const;
    Time GetSceneDuration() const;

  private:
    struct NodeRecord
    {
        std::string id;
        std::string state;
    };

    struct ChannelRecord
    {
        std::string id;
        std::string src;
        std::string dst;
        double nominalCapacityMbps{0.0};
        double effectiveCapacityMbps{0.0};
        double simulationCapacityMbps{0.0};
        std::string state;
        std::vector<std::string> interfaceIds;
    };

    struct InterfaceRecord
    {
        std::string id;
        std::string node;
        uint32_t interfaceIndex{0};
        std::string channelId;
        std::string ipCidr;
        std::string mac;
        std::string queuePolicy;
        uint32_t queueSizePackets{0};
        std::string state;
    };

    struct FlowRecord
    {
        std::string id;
        std::string src;
        std::string dst;
        double nominalDemandMbps{0.0};
        double simulationDemandMbps{0.0};
        uint16_t port{0};
    };

    struct EventRecord
    {
        std::string id;
        Time time;
        std::string entityType;
        std::string entityId;
        std::string eventType;
        double rateMultiplier{1.0};
    };

    struct FlowRuntime
    {
        NetworkSceneTrafficPattern basePattern;
        Ptr<Application> sourceApplication;
        double rateMultiplier{1.0};
    };

    struct PacketCounters
    {
        uint64_t txPackets{0};
        uint64_t rxPackets{0};
        uint64_t dropPackets{0};
        uint64_t txDropPackets{0};
        uint64_t rxDropPackets{0};
        uint64_t txBytes{0};
        uint64_t rxBytes{0};
        uint64_t dropBytes{0};
    };

    void TraceIpv4Tx(Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t interface);
    void TraceIpv4Rx(Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t interface);
    void TraceIpv4Drop(const Ipv4Header& header,
                       Ptr<const Packet> packet,
                       Ipv4L3Protocol::DropReason reason,
                       Ptr<Ipv4> ipv4,
                       uint32_t interface);
    void TraceQueueDiscDrop(std::string interfaceId, Ptr<const QueueDiscItem> item);
    void TraceDeviceTxDrop(std::string interfaceId, Ptr<const Packet> packet);
    void TraceDeviceRxDrop(std::string interfaceId, Ptr<const Packet> packet);
    uint32_t GetCurrentQueuePackets(const std::string& interfaceId) const;
    void ResetForScene(const NetworkSceneData& scene);
    void LoadSceneRecords(const NetworkSceneData& scene);
    void InstallInternetStackAndTracing();
    void InstallSceneChannels(const std::vector<NetworkSceneChannelRow>& channels,
                           const std::vector<NetworkSceneNicRow>& nics,
                           std::unordered_map<std::string, std::string>& primaryAddressByNode,
                           std::vector<std::pair<std::string, uint32_t>>& disabledInterfaces);
    void InstallSceneRoutes(const std::vector<NetworkSceneNodeRow>& nodes,
                            const std::unordered_map<std::string, std::string>& primaryAddressByNode);
    void ApplyInitialDisabledStates(const std::vector<NetworkSceneNodeRow>& nodes,
                                    const std::vector<std::pair<std::string, uint32_t>>& disabledInterfaces);
    void ScheduleSceneEvents();
    void InstallSceneTraffic(const std::vector<NetworkSceneTrafficPattern>& traffic,
                             const std::unordered_map<std::string, std::string>& primaryAddressByNode);
    void InstallFlowMonitor();
    void ApplySceneEvent(std::string entityType, std::string entityId, std::string eventType, double rateMultiplier);
    void ApplyFlowRateEvent(const std::string& flowId, const std::string& eventType, double rateMultiplier);
    void ReconcileInterfaceStates();
    bool IsChannelOperational(const ChannelRecord& channel) const;
    bool IsInterfaceOperational(const InterfaceRecord& iface) const;

    std::string m_sceneDirectory;
    std::string m_sceneName;
    std::string m_eventFile;
    std::string m_resultPath;
    Time m_applicationStartTime;
    Time m_applicationStopTime;
    Time m_defaultChannelDelay;
    uint32_t m_packetSize;
    double m_valueScaleFactor;

    NodeContainer m_nodes;
    NetDeviceContainer m_devices;
    ApplicationContainer m_applications;
    uint32_t m_channelCount;
    uint32_t m_flowCount;
    Time m_sceneDuration;

    std::vector<NodeRecord> m_nodeRecords;
    std::vector<ChannelRecord> m_channelRecords;
    std::vector<InterfaceRecord> m_interfaceRecords;
    std::vector<FlowRecord> m_flowRecords;
    std::vector<EventRecord> m_eventRecords;
    std::vector<std::vector<int>> m_routingMatrix;
    std::map<std::string, uint32_t> m_nodeIndexById;
    std::map<std::string, uint32_t> m_channelIndexById;
    std::map<std::string, uint32_t> m_interfaceIndexById;
    std::map<std::string, uint32_t> m_flowIndexById;
    std::map<std::string, uint32_t> m_ipv4InterfaceById;
    std::map<uint32_t, std::string> m_nodeIdByNs3Node;
    std::map<std::pair<std::string, uint32_t>, std::string> m_interfaceIdByNodeInterface;
    std::map<std::string, std::string> m_peerInterfaceById;
    std::map<std::string, PacketCounters> m_nodeCounters;
    std::map<std::string, PacketCounters> m_interfaceCounters;
    std::map<std::string, Ptr<QueueDisc>> m_queueDiscs;
    std::map<std::string, FlowRuntime> m_flowRuntimeById;
    std::unique_ptr<FlowMonitorHelper> m_flowMonitorHelper;
    Ptr<FlowMonitor> m_flowMonitor;
};

} // namespace ns3

#endif /* NETWORK_SCENE_HELPER_H */
