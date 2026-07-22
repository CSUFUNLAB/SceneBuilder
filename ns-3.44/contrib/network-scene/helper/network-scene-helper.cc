#include "network-scene-helper.h"

#include "ns3/network-scene-reader.h"
#include "ns3/network-scene-topology.h"
#include "ns3/network-scene-traffic.h"

#include "ns3/data-rate.h"
#include "ns3/flow-monitor-helper.h"
#include "ns3/inet-socket-address.h"
#include "ns3/internet-stack-helper.h"
#include "ns3/ipv4.h"
#include "ns3/ipv4-address.h"
#include "ns3/ipv4-flow-classifier.h"
#include "ns3/ipv4-interface-address.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/ipv4-static-routing.h"
#include "ns3/log.h"
#include "ns3/mac48-address.h"
#include "ns3/node.h"
#include "ns3/on-off-helper.h"
#include "ns3/packet-sink-helper.h"
#include "ns3/point-to-point-helper.h"
#include "ns3/point-to-point-net-device.h"
#include "ns3/queue-disc.h"
#include "ns3/simulator.h"
#include "ns3/string.h"
#include "ns3/traffic-control-helper.h"
#include "ns3/uinteger.h"
#include "ns3/udp-socket-factory.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("NetworkSceneHelper");

namespace
{

constexpr bool LEGACY_RUNTIME_EVENTS_ENABLED = false;

std::pair<std::string, uint32_t>
SplitCidr(const std::string& value)
{
    auto pos = value.find('/');
    if (pos == std::string::npos)
    {
        return {value, 32};
    }
    return {value.substr(0, pos), static_cast<uint32_t>(std::stoul(value.substr(pos + 1)))};
}

std::string
MaskFromPrefix(uint32_t prefix)
{
    if (prefix > 32)
    {
        throw std::runtime_error("Invalid IPv4 prefix length");
    }
    uint32_t mask = prefix == 0 ? 0 : (0xffffffffu << (32 - prefix));
    std::ostringstream out;
    out << ((mask >> 24) & 0xff) << "." << ((mask >> 16) & 0xff) << "."
        << ((mask >> 8) & 0xff) << "." << (mask & 0xff);
    return out.str();
}

uint64_t
MbpsToBps(double mbps)
{
    if (mbps <= 0.0)
    {
        return 1;
    }
    return static_cast<uint64_t>(std::llround(mbps * 1000000.0));
}

std::string
QueueDiscTypeForPolicy(const std::string& queuePolicy)
{
    if (queuePolicy == "RED")
    {
        return "ns3::RedQueueDisc";
    }
    if (queuePolicy == "CoDel")
    {
        return "ns3::CoDelQueueDisc";
    }
    if (queuePolicy == "FqCoDel")
    {
        return "ns3::FqCoDelQueueDisc";
    }
    return "ns3::PfifoFastQueueDisc";
}

void
ApplyEventInitialStates(NetworkSceneData& scene)
{
    std::map<std::pair<std::string, std::string>, const NetworkSceneEventRow*> firstEvents;
    for (const auto& event : scene.events)
    {
        if (event.eventType != "fault" && event.eventType != "recovery")
        {
            continue;
        }
        const auto key = std::make_pair(event.entityType, event.entityId);
        auto [it, inserted] = firstEvents.emplace(key, &event);
        if (!inserted && event.timeSeconds < it->second->timeSeconds)
        {
            it->second = &event;
        }
    }

    for (const auto& [key, event] : firstEvents)
    {
        const std::string initialState = event->eventType == "recovery" ? "disabled" : "normal";
        const auto setState = [&](auto& rows) {
            auto row = std::find_if(rows.begin(), rows.end(), [&](const auto& candidate) {
                return candidate.id == event->entityId;
            });
            if (row != rows.end())
            {
                row->state = initialState;
            }
        };

        if (key.first == "node")
        {
            setState(scene.nodes);
        }
        else if (key.first == "channel")
        {
            setState(scene.channels);
        }
        else if (key.first == "nic")
        {
            setState(scene.nics);
        }
    }
}

} // namespace

NetworkSceneHelper::NetworkSceneHelper()
    : m_sceneDirectory("scenes/example_id0001_Abvt_t300s"),
      m_sceneName("example_id0001_Abvt_t300s"),
      m_eventFile(""),
      m_resultPath(""),
      m_applicationStartTime(Seconds(1.0)),
      m_applicationStopTime(Seconds(0.0)),
      m_defaultChannelDelay(MilliSeconds(1)),
      m_packetSize(1024),
      m_valueScaleFactor(1.0),
      m_channelCount(0),
      m_flowCount(0),
      m_sceneDuration(Seconds(300.0))
{
}

NetworkSceneHelper::~NetworkSceneHelper() = default;

void
NetworkSceneHelper::SetSceneDirectory(const std::string& sceneDirectory)
{
    m_sceneDirectory = ResolveNetworkSceneDirectory(sceneDirectory);
    m_sceneName = NetworkSceneBaseName(m_sceneDirectory);
}

void
NetworkSceneHelper::SetApplicationStartTime(Time startTime)
{
    m_applicationStartTime = startTime;
}

void
NetworkSceneHelper::SetApplicationStopTime(Time stopTime)
{
    m_applicationStopTime = stopTime;
}

void
NetworkSceneHelper::SetDefaultChannelDelay(Time delay)
{
    m_defaultChannelDelay = delay;
}

void
NetworkSceneHelper::SetPacketSize(uint32_t packetSize)
{
    m_packetSize = packetSize;
}

void
NetworkSceneHelper::SetValueScaleFactor(double scaleFactor)
{
    m_valueScaleFactor = std::max(1.0, scaleFactor);
}

void
NetworkSceneHelper::SetEventFile(const std::string& eventFile)
{
    if (!LEGACY_RUNTIME_EVENTS_ENABLED && !eventFile.empty())
    {
        throw std::runtime_error(
            "Runtime event files are disabled; provide a separate paired scene instead");
    }
    m_eventFile = eventFile;
}

void
NetworkSceneHelper::SetResultPath(const std::string& resultPath)
{
    m_resultPath = resultPath;
}

void
NetworkSceneHelper::Install()
{
    auto scene = ReadNetworkSceneData(
        m_sceneDirectory,
        LEGACY_RUNTIME_EVENTS_ENABLED ? m_eventFile : std::string());
    if (LEGACY_RUNTIME_EVENTS_ENABLED)
    {
        ApplyEventInitialStates(scene);
    }

    ResetForScene(scene);
    LoadSceneRecords(scene);
    InstallInternetStackAndTracing();

    std::unordered_map<std::string, std::string> primaryAddressByNode;
    std::vector<std::pair<std::string, uint32_t>> disabledInterfaces;
    InstallSceneChannels(scene.channels, scene.nics, primaryAddressByNode, disabledInterfaces);
    InstallSceneRoutes(scene.nodes, primaryAddressByNode);
    ApplyInitialDisabledStates(scene.nodes, disabledInterfaces);
    if (LEGACY_RUNTIME_EVENTS_ENABLED)
    {
        ScheduleSceneEvents();
    }
    InstallSceneTraffic(scene.traffic, primaryAddressByNode);
    InstallFlowMonitor();
}

void
NetworkSceneHelper::ResetForScene(const NetworkSceneData& scene)
{
    m_sceneDuration = Seconds(scene.sceneDurationSeconds);
    if (m_applicationStopTime.IsZero())
    {
        m_applicationStopTime = m_sceneDuration;
    }

    m_nodes = NodeContainer();
    m_devices = NetDeviceContainer();
    m_applications = ApplicationContainer();
    m_channelCount = static_cast<uint32_t>(scene.channels.size());
    m_flowCount = static_cast<uint32_t>(scene.traffic.size());
    m_nodeRecords.clear();
    m_channelRecords.clear();
    m_interfaceRecords.clear();
    m_flowRecords.clear();
    m_eventRecords.clear();
    m_routingMatrix = scene.routingMatrix;
    m_nodeIndexById.clear();
    m_channelIndexById.clear();
    m_interfaceIndexById.clear();
    m_flowIndexById.clear();
    m_ipv4InterfaceById.clear();
    m_nodeIdByNs3Node.clear();
    m_interfaceIdByNodeInterface.clear();
    m_peerInterfaceById.clear();
    m_nodeCounters.clear();
    m_interfaceCounters.clear();
    m_queueDiscs.clear();
    m_flowRuntimeById.clear();
    m_flowMonitorHelper.reset();
    m_flowMonitor = nullptr;
}

void
NetworkSceneHelper::LoadSceneRecords(const NetworkSceneData& scene)
{
    m_nodes.Create(scene.nodes.size());

    for (uint32_t i = 0; i < scene.nodes.size(); ++i)
    {
        m_nodeIndexById[scene.nodes[i].id] = i;
        m_nodeRecords.push_back({scene.nodes[i].id, scene.nodes[i].state});
        m_nodeIdByNs3Node[m_nodes.Get(i)->GetId()] = scene.nodes[i].id;
    }
    for (const auto& channel : scene.channels)
    {
        const double effectiveCapacityMbps = channel.bandwidthMbps * channel.capacityMultiplier;
        const double simulationCapacityMbps = effectiveCapacityMbps / m_valueScaleFactor;
        m_channelRecords.push_back({channel.id,
                                   channel.src,
                                   channel.dst,
                                   channel.bandwidthMbps,
                                   effectiveCapacityMbps,
                                   simulationCapacityMbps,
                                   channel.state,
                                   {}});
    }
    std::map<std::string, uint32_t> channelRecordIndex;
    for (uint32_t i = 0; i < m_channelRecords.size(); ++i)
    {
        channelRecordIndex[m_channelRecords[i].id] = i;
        m_channelIndexById[m_channelRecords[i].id] = i;
    }
    for (const auto& nic : scene.nics)
    {
        m_interfaceRecords.push_back({nic.id,
                                      nic.node,
                                      nic.interfaceIndex,
                                      nic.channelId,
                                      nic.ipCidr,
                                      nic.mac,
                                      nic.queuePolicy,
                                      nic.queueSizePackets,
                                      nic.state});
        m_interfaceIndexById[nic.id] = static_cast<uint32_t>(m_interfaceRecords.size() - 1);
        m_interfaceIdByNodeInterface[{nic.node, nic.interfaceIndex}] = nic.id;
        auto channelIt = channelRecordIndex.find(nic.channelId);
        if (channelIt != channelRecordIndex.end())
        {
            m_channelRecords[channelIt->second].interfaceIds.push_back(nic.id);
        }
    }
    for (const auto& channel : m_channelRecords)
    {
        if (channel.interfaceIds.size() == 2)
        {
            m_peerInterfaceById[channel.interfaceIds[0]] = channel.interfaceIds[1];
            m_peerInterfaceById[channel.interfaceIds[1]] = channel.interfaceIds[0];
        }
    }
    if (LEGACY_RUNTIME_EVENTS_ENABLED)
    {
        for (const auto& event : scene.events)
        {
            m_eventRecords.push_back({event.id,
                                      Seconds(std::max(0.0, event.timeSeconds)),
                                      event.entityType,
                                      event.entityId,
                                      event.eventType,
                                      event.rateMultiplier});
        }
    }
}

void
NetworkSceneHelper::InstallInternetStackAndTracing()
{
    InternetStackHelper internet;
    internet.Install(m_nodes);
    for (uint32_t i = 0; i < m_nodes.GetN(); ++i)
    {
        Ptr<Ipv4> ipv4 = m_nodes.Get(i)->GetObject<Ipv4>();
        ipv4->TraceConnectWithoutContext("Tx", MakeCallback(&NetworkSceneHelper::TraceIpv4Tx, this));
        ipv4->TraceConnectWithoutContext("Rx", MakeCallback(&NetworkSceneHelper::TraceIpv4Rx, this));
        ipv4->TraceConnectWithoutContext("Drop", MakeCallback(&NetworkSceneHelper::TraceIpv4Drop, this));
    }
}

void
NetworkSceneHelper::InstallSceneChannels(const std::vector<NetworkSceneChannelRow>& channels,
                                      const std::vector<NetworkSceneNicRow>& nics,
                                      std::unordered_map<std::string, std::string>& primaryAddressByNode,
                                      std::vector<std::pair<std::string, uint32_t>>& disabledInterfaces)
{
    const auto nicsByChannel = GroupNetworkSceneNicsByChannel(nics);
    for (const auto& channel : channels)
    {
        auto channelNics = SortedNetworkSceneChannelNics(nicsByChannel, channel.id);

        PointToPointHelper p2p;
        const double simulationCapacityMbps =
            channel.bandwidthMbps * channel.capacityMultiplier / m_valueScaleFactor;
        p2p.SetDeviceAttribute("DataRate",
                               DataRateValue(DataRate(MbpsToBps(simulationCapacityMbps))));
        p2p.SetChannelAttribute("Delay", TimeValue(m_defaultChannelDelay));
        p2p.SetQueue("ns3::DropTailQueue", "MaxSize", StringValue("1p"));

        NodeContainer endpoints(m_nodes.Get(m_nodeIndexById.at(channelNics[0].node)),
                                m_nodes.Get(m_nodeIndexById.at(channelNics[1].node)));
        NetDeviceContainer devices = p2p.Install(endpoints);
        m_devices.Add(devices);

        for (uint32_t endpoint = 0; endpoint < 2; ++endpoint)
        {
            const auto& nic = channelNics[endpoint];
            Ptr<PointToPointNetDevice> device =
                DynamicCast<PointToPointNetDevice>(devices.Get(endpoint));
            if (device == nullptr)
            {
                throw std::runtime_error("Expected point-to-point device for " + nic.id);
            }
            device->TraceConnectWithoutContext(
                "MacTxDrop",
                MakeCallback(&NetworkSceneHelper::TraceDeviceTxDrop, this).Bind(nic.id));
            device->TraceConnectWithoutContext(
                "PhyTxDrop",
                MakeCallback(&NetworkSceneHelper::TraceDeviceTxDrop, this).Bind(nic.id));
            device->TraceConnectWithoutContext(
                "PhyRxDrop",
                MakeCallback(&NetworkSceneHelper::TraceDeviceRxDrop, this).Bind(nic.id));

            TrafficControlHelper trafficControl;
            trafficControl.SetRootQueueDisc(QueueDiscTypeForPolicy(nic.queuePolicy),
                                            "MaxSize",
                                            StringValue(std::to_string(std::max(1u, nic.queueSizePackets)) + "p"));
            QueueDiscContainer queueDiscs = trafficControl.Install(devices.Get(endpoint));
            if (queueDiscs.GetN() != 1)
            {
                throw std::runtime_error("Expected one root queue disc for " + nic.id);
            }
            m_queueDiscs[nic.id] = queueDiscs.Get(0);
            m_queueDiscs[nic.id]->TraceConnectWithoutContext(
                "Drop",
                MakeCallback(&NetworkSceneHelper::TraceQueueDiscDrop, this).Bind(nic.id));

            Ptr<Node> node = m_nodes.Get(m_nodeIndexById.at(nic.node));
            Ptr<Ipv4> ipv4 = node->GetObject<Ipv4>();
            devices.Get(endpoint)->SetAddress(Mac48Address(nic.mac.c_str()));
            uint32_t ifIndex = ipv4->AddInterface(devices.Get(endpoint));
            if (ifIndex != nic.interfaceIndex)
            {
                throw std::runtime_error("IPv4 interface index mismatch for " + nic.id);
            }

            const auto [address, prefix] = SplitCidr(nic.ipCidr);
            ipv4->AddAddress(ifIndex,
                             Ipv4InterfaceAddress(Ipv4Address(address.c_str()),
                                                  Ipv4Mask(MaskFromPrefix(prefix).c_str())));
            ipv4->SetMetric(ifIndex, 1);
            ipv4->SetUp(ifIndex);
            m_ipv4InterfaceById[nic.id] = ifIndex;
            if (primaryAddressByNode.find(nic.node) == primaryAddressByNode.end())
            {
                primaryAddressByNode[nic.node] = address;
            }
            if (nic.state == "disabled" || channel.state == "disabled")
            {
                disabledInterfaces.push_back({nic.node, ifIndex});
            }
        }
    }
}

void
NetworkSceneHelper::InstallSceneRoutes(const std::vector<NetworkSceneNodeRow>& nodes,
                                       const std::unordered_map<std::string, std::string>& primaryAddressByNode)
{
    Ipv4StaticRoutingHelper staticRoutingHelper;
    for (uint32_t src = 0; src < nodes.size(); ++src)
    {
        if (src >= m_routingMatrix.size())
        {
            break;
        }
        Ptr<Ipv4> ipv4 = m_nodes.Get(src)->GetObject<Ipv4>();
        Ptr<Ipv4StaticRouting> staticRouting = staticRoutingHelper.GetStaticRouting(ipv4);
        if (!staticRouting)
        {
            continue;
        }
        for (uint32_t dst = 0; dst < nodes.size(); ++dst)
        {
            if (src == dst || dst >= m_routingMatrix[src].size())
            {
                continue;
            }
            int outInterface = m_routingMatrix[src][dst];
            if (outInterface <= 0)
            {
                continue;
            }
            auto addressIt = primaryAddressByNode.find(nodes[dst].id);
            if (addressIt == primaryAddressByNode.end())
            {
                continue;
            }
            staticRouting->AddHostRouteTo(Ipv4Address(addressIt->second.c_str()),
                                          static_cast<uint32_t>(outInterface));
        }
    }
}

void
NetworkSceneHelper::ApplyInitialDisabledStates(
    const std::vector<NetworkSceneNodeRow>& nodes,
    const std::vector<std::pair<std::string, uint32_t>>& disabledInterfaces)
{
    auto allDisabledInterfaces = disabledInterfaces;
    for (uint32_t i = 0; i < nodes.size(); ++i)
    {
        if (nodes[i].state != "disabled")
        {
            continue;
        }
        Ptr<Ipv4> ipv4 = m_nodes.Get(i)->GetObject<Ipv4>();
        for (uint32_t iface = 1; iface < ipv4->GetNInterfaces(); ++iface)
        {
            allDisabledInterfaces.push_back({nodes[i].id, iface});
        }
    }

    for (const auto& item : allDisabledInterfaces)
    {
        Ptr<Ipv4> ipv4 = m_nodes.Get(m_nodeIndexById.at(item.first))->GetObject<Ipv4>();
        ipv4->SetDown(item.second);
    }
    ReconcileInterfaceStates();
}

void
NetworkSceneHelper::ScheduleSceneEvents()
{
    for (const auto& event : m_eventRecords)
    {
        if (event.time > m_applicationStopTime)
        {
            continue;
        }
        Simulator::Schedule(event.time,
                            &NetworkSceneHelper::ApplySceneEvent,
                            this,
                            event.entityType,
                            event.entityId,
                            event.eventType,
                            event.rateMultiplier);
    }
}

void
NetworkSceneHelper::InstallSceneTraffic(const std::vector<NetworkSceneTrafficPattern>& traffic,
                                        const std::unordered_map<std::string, std::string>& primaryAddressByNode)
{
    uint32_t flowIndex = 0;
    for (const auto& flow : traffic)
    {
        const double scaledDemandMbps = flow.demandMbps / m_valueScaleFactor;
        if (scaledDemandMbps <= 0.0)
        {
            continue;
        }
        auto srcIt = m_nodeIndexById.find(flow.src);
        auto dstIt = m_nodeIndexById.find(flow.dst);
        auto dstAddressIt = primaryAddressByNode.find(flow.dst);
        if (srcIt == m_nodeIndexById.end() || dstIt == m_nodeIndexById.end() ||
            dstAddressIt == primaryAddressByNode.end())
        {
            continue;
        }

        uint16_t port = static_cast<uint16_t>(10000 + (flowIndex % 50000));
        Address sinkAddress(InetSocketAddress(Ipv4Address::GetAny(), port));
        PacketSinkHelper sinkHelper("ns3::UdpSocketFactory", sinkAddress);
        ApplicationContainer sink = sinkHelper.Install(m_nodes.Get(dstIt->second));
        sink.Start(Seconds(0.0));
        sink.Stop(m_applicationStopTime);
        m_applications.Add(sink);

        Address remoteAddress(InetSocketAddress(Ipv4Address(dstAddressIt->second.c_str()), port));
        NetworkSceneTrafficPattern pattern;
        pattern.id = flow.id;
        pattern.src = flow.src;
        pattern.dst = flow.dst;
        pattern.demandMbps = scaledDemandMbps;
        pattern.featureModel = flow.featureModel;
        pattern.paramLambda = flow.paramLambda;
        pattern.paramOnMean = flow.paramOnMean;
        pattern.paramOffMean = flow.paramOffMean;
        pattern.paramPeakRateMbps = flow.paramPeakRateMbps / m_valueScaleFactor;
        ApplicationContainer source = InstallNetworkSceneTrafficSource(m_nodes.Get(srcIt->second),
                                                                       remoteAddress,
                                                                       pattern,
                                                                       m_packetSize,
                                                                       m_applicationStartTime,
                                                                       m_applicationStopTime);
        m_applications.Add(source);
        if (source.GetN() > 0)
        {
            m_flowRuntimeById[flow.id] = {pattern, source.Get(0), 1.0};
        }
        m_flowIndexById[flow.id] = static_cast<uint32_t>(m_flowRecords.size());
        m_flowRecords.push_back({flow.id, flow.src, flow.dst, flow.demandMbps, scaledDemandMbps, port});
        ++flowIndex;
    }
}

void
NetworkSceneHelper::InstallFlowMonitor()
{
    m_flowMonitorHelper = std::make_unique<FlowMonitorHelper>();
    m_flowMonitor = m_flowMonitorHelper->Install(m_nodes);
}

bool
NetworkSceneHelper::IsChannelOperational(const ChannelRecord& channel) const
{
    if (channel.state == "disabled" || channel.interfaceIds.size() != 2)
    {
        return false;
    }

    for (const auto& interfaceId : channel.interfaceIds)
    {
        auto interfaceIt = m_interfaceIndexById.find(interfaceId);
        if (interfaceIt == m_interfaceIndexById.end())
        {
            return false;
        }

        const auto& iface = m_interfaceRecords[interfaceIt->second];
        if (iface.state == "disabled")
        {
            return false;
        }

        auto nodeIt = m_nodeIndexById.find(iface.node);
        if (nodeIt == m_nodeIndexById.end() || m_nodeRecords[nodeIt->second].state == "disabled")
        {
            return false;
        }
    }

    return true;
}

bool
NetworkSceneHelper::IsInterfaceOperational(const InterfaceRecord& iface) const
{
    if (iface.state == "disabled")
    {
        return false;
    }

    auto nodeIt = m_nodeIndexById.find(iface.node);
    if (nodeIt == m_nodeIndexById.end() || m_nodeRecords[nodeIt->second].state == "disabled")
    {
        return false;
    }

    auto channelIt = m_channelIndexById.find(iface.channelId);
    if (channelIt == m_channelIndexById.end() ||
        !IsChannelOperational(m_channelRecords[channelIt->second]))
    {
        return false;
    }

    return true;
}

void
NetworkSceneHelper::ReconcileInterfaceStates()
{
    for (const auto& iface : m_interfaceRecords)
    {
        auto nodeIt = m_nodeIndexById.find(iface.node);
        auto ipv4It = m_ipv4InterfaceById.find(iface.id);
        if (nodeIt == m_nodeIndexById.end() || ipv4It == m_ipv4InterfaceById.end())
        {
            continue;
        }

        Ptr<Ipv4> ipv4 = m_nodes.Get(nodeIt->second)->GetObject<Ipv4>();
        if (IsInterfaceOperational(iface))
        {
            ipv4->SetUp(ipv4It->second);
        }
        else
        {
            ipv4->SetDown(ipv4It->second);
        }
    }
}

void
NetworkSceneHelper::ApplySceneEvent(std::string entityType,
                                    std::string entityId,
                                    std::string eventType,
                                    double rateMultiplier)
{
    if (entityType == "data_flow")
    {
        ApplyFlowRateEvent(entityId, eventType, rateMultiplier);
        return;
    }

    const std::string nextState = eventType == "recovery" ? "normal" : "disabled";

    if (entityType == "node")
    {
        auto it = m_nodeIndexById.find(entityId);
        if (it != m_nodeIndexById.end())
        {
            m_nodeRecords[it->second].state = nextState;
        }
    }
    else if (entityType == "channel")
    {
        auto it = m_channelIndexById.find(entityId);
        if (it != m_channelIndexById.end())
        {
            m_channelRecords[it->second].state = nextState;
        }
    }
    else if (entityType == "nic")
    {
        auto it = m_interfaceIndexById.find(entityId);
        if (it != m_interfaceIndexById.end())
        {
            m_interfaceRecords[it->second].state = nextState;
        }
    }

    ReconcileInterfaceStates();
}

void
NetworkSceneHelper::ApplyFlowRateEvent(const std::string& flowId, const std::string& eventType, double rateMultiplier)
{
    auto runtimeIt = m_flowRuntimeById.find(flowId);
    if (runtimeIt == m_flowRuntimeById.end())
    {
        return;
    }

    if (rateMultiplier <= 0.0)
    {
        rateMultiplier = 1.0;
    }
    if (eventType == "decrease")
    {
        rateMultiplier = std::min(rateMultiplier, 1.0);
    }
    else if (eventType == "increase")
    {
        rateMultiplier = std::max(rateMultiplier, 1.0);
    }
    else
    {
        return;
    }

    auto& runtime = runtimeIt->second;
    runtime.rateMultiplier = rateMultiplier;
    if (!UpdateNetworkSceneTrafficSourceRate(runtime.sourceApplication,
                                             runtime.basePattern,
                                             runtime.rateMultiplier,
                                             m_packetSize))
    {
        return;
    }

    auto flowIt = m_flowIndexById.find(flowId);
    if (flowIt != m_flowIndexById.end() && flowIt->second < m_flowRecords.size())
    {
        auto& flow = m_flowRecords[flowIt->second];
        flow.simulationDemandMbps = runtime.basePattern.demandMbps * runtime.rateMultiplier;
        flow.nominalDemandMbps = flow.simulationDemandMbps * m_valueScaleFactor;
    }
}

void
NetworkSceneHelper::TraceIpv4Tx(Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t interface)
{
    const std::string nodeId = m_nodeIdByNs3Node[ipv4->GetObject<Node>()->GetId()];
    auto& node = m_nodeCounters[nodeId];
    node.txPackets++;
    node.txBytes += packet->GetSize();

    auto ifaceIt = m_interfaceIdByNodeInterface.find({nodeId, interface});
    if (ifaceIt != m_interfaceIdByNodeInterface.end())
    {
        auto& iface = m_interfaceCounters[ifaceIt->second];
        iface.txPackets++;
        iface.txBytes += packet->GetSize();
    }
}

void
NetworkSceneHelper::TraceIpv4Rx(Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t interface)
{
    const std::string nodeId = m_nodeIdByNs3Node[ipv4->GetObject<Node>()->GetId()];
    auto& node = m_nodeCounters[nodeId];
    node.rxPackets++;
    node.rxBytes += packet->GetSize();

    auto ifaceIt = m_interfaceIdByNodeInterface.find({nodeId, interface});
    if (ifaceIt != m_interfaceIdByNodeInterface.end())
    {
        auto& iface = m_interfaceCounters[ifaceIt->second];
        iface.rxPackets++;
        iface.rxBytes += packet->GetSize();
    }
}

uint32_t
NetworkSceneHelper::GetCurrentQueuePackets(const std::string& interfaceId) const
{
    auto queueIt = m_queueDiscs.find(interfaceId);
    if (queueIt == m_queueDiscs.end() || queueIt->second == nullptr)
    {
        return 0;
    }
    return queueIt->second->GetNPackets();
}

void
NetworkSceneHelper::TraceIpv4Drop(const Ipv4Header& header,
                                  Ptr<const Packet> packet,
                                  Ipv4L3Protocol::DropReason reason,
                                  Ptr<Ipv4> ipv4,
                                  uint32_t interface)
{
    (void)header;
    const std::string nodeId = m_nodeIdByNs3Node[ipv4->GetObject<Node>()->GetId()];
    auto& node = m_nodeCounters[nodeId];
    node.dropPackets++;
    node.dropBytes += packet->GetSize();

    auto ifaceIt = m_interfaceIdByNodeInterface.find({nodeId, interface});
    if (ifaceIt == m_interfaceIdByNodeInterface.end())
    {
        return;
    }

    auto& iface = m_interfaceCounters[ifaceIt->second];
    if (reason == Ipv4L3Protocol::DROP_TTL_EXPIRED)
    {
        iface.txDropPackets++;
    }
    else
    {
        iface.rxDropPackets++;
    }
}

void
NetworkSceneHelper::TraceQueueDiscDrop(std::string interfaceId, Ptr<const QueueDiscItem> item)
{
    (void)item;
    m_interfaceCounters[interfaceId].txDropPackets++;
}

void
NetworkSceneHelper::TraceDeviceTxDrop(std::string interfaceId, Ptr<const Packet> packet)
{
    (void)packet;
    m_interfaceCounters[interfaceId].txDropPackets++;
}

void
NetworkSceneHelper::TraceDeviceRxDrop(std::string interfaceId, Ptr<const Packet> packet)
{
    (void)packet;
    m_interfaceCounters[interfaceId].rxDropPackets++;
}


NodeContainer
NetworkSceneHelper::GetNodes() const
{
    return m_nodes;
}

ApplicationContainer
NetworkSceneHelper::GetApplications() const
{
    return m_applications;
}

uint32_t
NetworkSceneHelper::GetNodeCount() const
{
    return m_nodes.GetN();
}

uint32_t
NetworkSceneHelper::GetChannelCount() const
{
    return m_channelCount;
}

uint32_t
NetworkSceneHelper::GetFlowCount() const
{
    return m_flowCount;
}

Time
NetworkSceneHelper::GetSceneDuration() const
{
    return m_sceneDuration;
}

} // namespace ns3
