#include "network-scene-helper.h"

#include "ns3/flow-monitor-helper.h"
#include "ns3/ipv4-flow-classifier.h"
#include "ns3/network-scene-topology.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace ns3
{

namespace
{

constexpr double SATURATION_THRESHOLD = 0.95;

std::string
JsonEscape(const std::string& value)
{
    std::ostringstream out;
    for (char c : value)
    {
        switch (c)
        {
        case '"':
            out << "\\\"";
            break;
        case '\\':
            out << "\\\\";
            break;
        case '\b':
            out << "\\b";
            break;
        case '\f':
            out << "\\f";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            out << c;
            break;
        }
    }
    return out.str();
}

std::string
JsonString(const std::string& value)
{
    return "\"" + JsonEscape(value) + "\"";
}

std::string
JsonStringArray(const std::vector<std::string>& values)
{
    std::ostringstream out;
    out << "[";
    for (std::size_t i = 0; i < values.size(); ++i)
    {
        if (i > 0)
        {
            out << ",";
        }
        out << JsonString(values[i]);
    }
    out << "]";
    return out.str();
}

std::string
JsonRawArray(const std::vector<std::string>& values)
{
    std::ostringstream out;
    out << "[";
    for (std::size_t i = 0; i < values.size(); ++i)
    {
        if (i > 0)
        {
            out << ",";
        }
        out << values[i];
    }
    out << "]";
    return out.str();
}

std::string
ScopedLocalId(const std::string& ownerId, const std::string& prefix, uint32_t index)
{
    std::ostringstream out;
    out << ownerId << ":" << prefix << std::setw(6) << std::setfill('0') << index;
    return out.str();
}

double
SafeRatio(double numerator, double denominator)
{
    if (denominator <= 0.0)
    {
        return 0.0;
    }
    return numerator / denominator;
}

double
BoundedRatio(double numerator, double denominator)
{
    return std::min(1.0, std::max(0.0, SafeRatio(numerator, denominator)));
}

} // namespace

void
NetworkSceneHelper::WriteResults() const
{
    std::filesystem::path outputPath =
        m_resultPath.empty() ? std::filesystem::path(m_sceneDirectory) / "twin" / "0.jsonl"
                             : std::filesystem::path(m_resultPath);
    if (outputPath.has_parent_path())
    {
        std::filesystem::create_directories(outputPath.parent_path());
    }
    std::ofstream output(outputPath);
    if (!output)
    {
        throw std::runtime_error("Cannot open result output file: " + outputPath.string());
    }

    const double duration = std::max(0.000001, (m_applicationStopTime - m_applicationStartTime).GetSeconds());
    std::map<uint16_t, FlowMonitor::FlowStats> statsByPort;
    if (m_flowMonitor)
    {
        m_flowMonitor->CheckForLostPackets();
        auto classifier = DynamicCast<Ipv4FlowClassifier>(m_flowMonitorHelper->GetClassifier());
        for (const auto& item : m_flowMonitor->GetFlowStats())
        {
            auto tuple = classifier->FindFlow(item.first);
            statsByPort[tuple.destinationPort] = item.second;
        }
    }

    uint64_t totalRxBytes = 0;
    uint64_t totalTxPackets = 0;
    uint64_t totalRxPackets = 0;
    uint64_t totalLostPackets = 0;
    uint32_t successfulFlows = 0;
    double delayPacketSumMs = 0.0;
    double utilizationSum = 0.0;
    double utilizationSquareSum = 0.0;
    uint32_t utilizationCount = 0;
    std::map<std::string, std::vector<std::string>> interfacesByNode;
    std::map<std::string, std::string> outputInterfaceIdById;
    std::map<std::string, ChannelRecord> channelById;
    std::map<std::string, std::string> peerInterfaceById;
    std::map<std::pair<std::string, uint32_t>, std::string> nextNodeByNodeInterface;
    std::map<std::string, std::vector<std::string>> routesByNode;
    std::map<std::string, std::vector<std::string>> actionsByNode;
    std::map<std::string, std::vector<std::string>> actionsByChannel;
    std::map<std::string, std::vector<std::string>> actionsByInterface;
    std::map<std::string, std::vector<std::string>> actionsByFlow;
    std::map<std::tuple<std::string, std::string, std::string>, std::vector<std::string>>
        routeDestinationsByKey;
    std::map<std::string, std::vector<std::string>> originatedFlowsByNode;

    auto eventActionJson = [](const EventRecord& event) {
        std::ostringstream action;
        action << "{\"event_id\":" << JsonString(event.id)
               << ",\"time\":" << event.time.GetSeconds()
               << ",\"event_type\":" << JsonString(event.eventType);
        if (std::abs(event.rateMultiplier - 1.0) > 0.000001)
        {
            action << ",\"rate_multiplier\":" << event.rateMultiplier;
        }
        action << "}";
        return action.str();
    };

    for (const auto& event : m_eventRecords)
    {
        if (event.entityType == "node")
        {
            actionsByNode[event.entityId].push_back(eventActionJson(event));
        }
        else if (event.entityType == "channel" || event.entityType == "link")
        {
            actionsByChannel[event.entityId].push_back(eventActionJson(event));
        }
        else if (event.entityType == "nic" || event.entityType == "interface")
        {
            actionsByInterface[event.entityId].push_back(eventActionJson(event));
        }
        else if (event.entityType == "data_flow" || event.entityType == "flow")
        {
            actionsByFlow[event.entityId].push_back(eventActionJson(event));
        }
    }

    for (const auto& channel : m_channelRecords)
    {
        channelById[channel.id] = channel;
        if (channel.interfaceIds.size() == 2)
        {
            peerInterfaceById[channel.interfaceIds[0]] = channel.interfaceIds[1];
            peerInterfaceById[channel.interfaceIds[1]] = channel.interfaceIds[0];
        }
    }
    for (const auto& iface : m_interfaceRecords)
    {
        outputInterfaceIdById[iface.id] =
            ScopedLocalId(iface.node, "IF", iface.interfaceIndex);
        interfacesByNode[iface.node].push_back(outputInterfaceIdById[iface.id]);
        auto channelIt = channelById.find(iface.channelId);
        if (channelIt != channelById.end())
        {
            const auto& channel = channelIt->second;
            std::string nextNode;
            if (channel.src == iface.node)
            {
                nextNode = channel.dst;
            }
            else if (channel.dst == iface.node)
            {
                nextNode = channel.src;
            }
            if (!nextNode.empty())
            {
                nextNodeByNodeInterface[{iface.node, iface.interfaceIndex}] = nextNode;
            }
        }
    }
    for (const auto& node : m_nodeRecords)
    {
        uint32_t srcIndex = NetworkSceneNodeNumber(node.id) - 1;
        if (srcIndex >= m_routingMatrix.size())
        {
            continue;
        }
        for (const auto& dstNode : m_nodeRecords)
        {
            if (node.id == dstNode.id)
            {
                continue;
            }
            uint32_t dstIndex = NetworkSceneNodeNumber(dstNode.id) - 1;
            if (dstIndex >= m_routingMatrix[srcIndex].size())
            {
                continue;
            }
            int outInterface = m_routingMatrix[srcIndex][dstIndex];
            if (outInterface <= 0)
            {
                continue;
            }
            auto ifaceIt = m_interfaceIdByNodeInterface.find({node.id, static_cast<uint32_t>(outInterface)});
            auto nextIt = nextNodeByNodeInterface.find({node.id, static_cast<uint32_t>(outInterface)});
            std::string egressInterface;
            if (ifaceIt != m_interfaceIdByNodeInterface.end())
            {
                auto outputIfaceIt = outputInterfaceIdById.find(ifaceIt->second);
                egressInterface = outputIfaceIt == outputInterfaceIdById.end()
                                      ? ifaceIt->second
                                      : outputIfaceIt->second;
            }
            const std::string nextHop = nextIt == nextNodeByNodeInterface.end() ? "" : nextIt->second;
            routeDestinationsByKey[{node.id, egressInterface, nextHop}].push_back(dstNode.id);
        }
    }
    for (const auto& item : routeDestinationsByKey)
    {
        const auto& [sourceNode, egressInterface, nextHop] = item.first;
        const auto& destinations = item.second;
        std::ostringstream route;
        route << "{\"destination_nodes\":" << JsonStringArray(destinations)
              << ",\"egress_interface\":";
        if (egressInterface.empty())
        {
            route << "null";
        }
        else
        {
            route << JsonString(egressInterface);
        }
        if (nextHop.empty())
        {
            route << ",\"next_hop\":null";
        }
        else
        {
            route << ",\"next_hop\":" << JsonString(nextHop);
        }
        route << "}";
        routesByNode[sourceNode].push_back(route.str());
    }
    for (const auto& flow : m_flowRecords)
    {
        originatedFlowsByNode[flow.src].push_back(flow.id);
    }
    auto buildPathNodes = [&](const std::string& src, const std::string& dst) {
        std::vector<std::string> path;
        path.push_back(src);
        if (src == dst)
        {
            return path;
        }

        std::string current = src;
        for (std::size_t hop = 0; hop < m_nodeRecords.size(); ++hop)
        {
            uint32_t srcIndex = NetworkSceneNodeNumber(current) - 1;
            uint32_t dstIndex = NetworkSceneNodeNumber(dst) - 1;
            if (srcIndex >= m_routingMatrix.size() || dstIndex >= m_routingMatrix[srcIndex].size())
            {
                break;
            }

            int outInterface = m_routingMatrix[srcIndex][dstIndex];
            if (outInterface <= 0)
            {
                break;
            }

            auto nextIt =
                nextNodeByNodeInterface.find({current, static_cast<uint32_t>(outInterface)});
            if (nextIt == nextNodeByNodeInterface.end())
            {
                break;
            }

            const std::string& nextNode = nextIt->second;
            if (std::find(path.begin(), path.end(), nextNode) != path.end())
            {
                break;
            }
            path.push_back(nextNode);
            if (nextNode == dst)
            {
                break;
            }
            current = nextNode;
        }
        return path;
    };

    for (const auto& iface : m_interfaceRecords)
    {
        auto counters = m_interfaceCounters.find(iface.id) == m_interfaceCounters.end()
                            ? PacketCounters{}
                            : m_interfaceCounters.at(iface.id);
        PacketCounters peerCounters;
        auto peerIt = peerInterfaceById.find(iface.id);
        if (peerIt != peerInterfaceById.end())
        {
            auto peerCountersIt = m_interfaceCounters.find(peerIt->second);
            if (peerCountersIt != m_interfaceCounters.end())
            {
                peerCounters = peerCountersIt->second;
            }
        }
        double txRateMbps =
            peerCounters.rxBytes * 8.0 / duration / 1000000.0 * m_valueScaleFactor;
        double rxRateMbps = counters.rxBytes * 8.0 / duration / 1000000.0 * m_valueScaleFactor;
        uint32_t currentQueuePackets = GetCurrentQueuePackets(iface.id);
        double queueUtilization = BoundedRatio(currentQueuePackets, iface.queueSizePackets);
        std::string state = "normal";
        if (!IsInterfaceOperational(iface))
        {
            state = "disabled";
        }
        else if (iface.state == "tx_failed" || iface.state == "rx_failed")
        {
            state = iface.state;
        }
        else if (queueUtilization >= SATURATION_THRESHOLD)
        {
            state = "saturated";
        }
        output << "{\"entity_type\":\"nic\",\"entity_id\":"
               << JsonString(outputInterfaceIdById[iface.id])
               << ",\"label\":" << JsonString(state)
               << ",\"properties\":{\"interface_index\":" << iface.interfaceIndex
               << ",\"rx_rate_mbps\":" << rxRateMbps
               << ",\"tx_rate_mbps\":" << txRateMbps
               << ",\"queue_policy\":" << JsonString(iface.queuePolicy)
               << ",\"queue_size_packets\":" << iface.queueSizePackets
               << ",\"queue_current_packets\":" << currentQueuePackets
               << ",\"rx_packets\":" << counters.rxPackets
               << ",\"tx_packets\":" << peerCounters.rxPackets
               << ",\"rx_drop_packets\":" << counters.rxDropPackets
               << ",\"tx_drop_packets\":" << counters.txDropPackets << "}"
               << ",\"relations\":{\"node\":" << JsonString(iface.node)
               << ",\"channel\":" << JsonString(iface.channelId) << "}";
        auto actionIt = actionsByInterface.find(iface.id);
        if (actionIt != actionsByInterface.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
    }

    for (const auto& node : m_nodeRecords)
    {
        auto counters = m_nodeCounters.find(node.id) == m_nodeCounters.end()
                            ? PacketCounters{}
                            : m_nodeCounters.at(node.id);
        output << "{\"entity_type\":\"node\",\"entity_id\":" << JsonString(node.id)
               << ",\"label\":" << JsonString(node.state)
               << ",\"properties\":{\"rx_packets\":" << counters.rxPackets
               << ",\"tx_packets\":" << counters.txPackets << "}"
               << ",\"relations\":{\"interfaces\":" << JsonStringArray(interfacesByNode[node.id])
               << ",\"originates_flows\":" << JsonStringArray(originatedFlowsByNode[node.id])
               << ",\"routes\":" << JsonRawArray(routesByNode[node.id]) << "}";
        auto actionIt = actionsByNode.find(node.id);
        if (actionIt != actionsByNode.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
    }

    for (const auto& channel : m_channelRecords)
    {
        double utilization = 0.0;
        std::vector<std::string> outputInterfaceIds;
        for (const auto& ifaceId : channel.interfaceIds)
        {
            auto outputIfaceIt = outputInterfaceIdById.find(ifaceId);
            outputInterfaceIds.push_back(outputIfaceIt == outputInterfaceIdById.end()
                                             ? ifaceId
                                             : outputIfaceIt->second);
            auto countersIt = m_interfaceCounters.find(ifaceId);
            if (countersIt != m_interfaceCounters.end())
            {
                double simulationRxRateMbps =
                    countersIt->second.rxBytes * 8.0 / duration / 1000000.0;
                utilization = std::max(
                    utilization,
                    BoundedRatio(simulationRxRateMbps, channel.simulationCapacityMbps));
            }
        }
        bool operational = IsChannelOperational(channel);
        double available = operational
                               ? std::max(0.0,
                                          channel.effectiveCapacityMbps * (1.0 - utilization))
                               : 0.0;
        std::string state = "normal";
        if (!operational)
        {
            state = "disabled";
        }
        else if (channel.state == "degraded")
        {
            state = "degraded";
        }
        else if (utilization >= SATURATION_THRESHOLD)
        {
            state = "saturated";
        }
        utilizationSum += utilization;
        utilizationSquareSum += utilization * utilization;
        utilizationCount++;
        output << "{\"entity_type\":\"channel\",\"entity_id\":" << JsonString(channel.id)
               << ",\"label\":" << JsonString(state)
               << ",\"properties\":{\"capacity_mbps\":" << channel.nominalCapacityMbps
               << ",\"available_bandwidth_mbps\":" << available
               << ",\"delay_ms\":" << m_defaultChannelDelay.GetMilliSeconds() << "}"
               << ",\"relations\":{\"connects\":" << JsonStringArray(outputInterfaceIds) << "}";
        auto actionIt = actionsByChannel.find(channel.id);
        if (actionIt != actionsByChannel.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
    }

    for (const auto& flow : m_flowRecords)
    {
        FlowMonitor::FlowStats stats;
        bool hasStats = statsByPort.find(flow.port) != statsByPort.end();
        if (hasStats)
        {
            stats = statsByPort[flow.port];
            totalRxBytes += stats.rxBytes;
            totalTxPackets += stats.txPackets;
            totalRxPackets += stats.rxPackets;
            totalLostPackets += stats.lostPackets;
            delayPacketSumMs += stats.delaySum.GetMilliSeconds();
        }
        double throughputMbps = hasStats
                                    ? stats.rxBytes * 8.0 / duration / 1000000.0 *
                                          m_valueScaleFactor
                                    : 0.0;
        double avgDelayMs = hasStats && stats.rxPackets > 0
                                ? stats.delaySum.GetMilliSeconds() / static_cast<double>(stats.rxPackets)
                                : 0.0;
        double lossRate = hasStats ? SafeRatio(stats.lostPackets, stats.txPackets) : 0.0;
        if (hasStats && stats.rxPackets > 0)
        {
            successfulFlows++;
        }
        std::string state = "normal";
        if (!hasStats || stats.txPackets == 0 || stats.rxPackets == 0)
        {
            state = "failed";
        }
        else if (lossRate > 0.0 || throughputMbps < flow.nominalDemandMbps * 0.8)
        {
            state = "degraded";
        }
        const auto pathNodes = buildPathNodes(flow.src, flow.dst);
        output << "{\"entity_type\":\"data_flow\",\"entity_id\":" << JsonString(flow.id)
               << ",\"label\":" << JsonString(state)
               << ",\"properties\":{\"demand_mbps\":" << flow.nominalDemandMbps
               << ",\"tx_packets\":" << (hasStats ? stats.txPackets : 0)
               << ",\"rx_packets\":" << (hasStats ? stats.rxPackets : 0)
               << ",\"lost_packets\":" << (hasStats ? stats.lostPackets : 0)
               << ",\"throughput_mbps\":" << throughputMbps
               << ",\"average_delay_ms\":" << avgDelayMs << "}"
               << ",\"relations\":{\"source_node\":" << JsonString(flow.src)
               << ",\"destination_node\":" << JsonString(flow.dst)
               << ",\"path_nodes\":" << JsonStringArray(pathNodes) << "}";
        auto actionIt = actionsByFlow.find(flow.id);
        if (actionIt != actionsByFlow.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
    }

    const double totalThroughputMbps = totalRxBytes * 8.0 / duration / 1000000.0;
    const double averageDelayMs = totalRxPackets > 0 ? delayPacketSumMs / totalRxPackets : 0.0;
    const double averageLoss = SafeRatio(totalLostPackets, totalTxPackets);
    const double reachabilityRatio = SafeRatio(successfulFlows, m_flowRecords.size());
    const double meanUtilization = utilizationCount > 0 ? utilizationSum / utilizationCount : 0.0;
    const double loadVariance =
        utilizationCount > 0 ? utilizationSquareSum / utilizationCount - meanUtilization * meanUtilization : 0.0;
    const std::string networkState = averageLoss > 0.05 || reachabilityRatio < 0.95 ? "degraded" : "normal";

    (void)totalThroughputMbps;
    (void)averageDelayMs;
    (void)averageLoss;
    (void)reachabilityRatio;
    (void)loadVariance;
    (void)networkState;
}

} // namespace ns3
