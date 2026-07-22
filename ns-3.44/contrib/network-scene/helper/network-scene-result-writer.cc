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
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <utility>
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
        m_resultPath.empty() ? std::filesystem::path(m_sceneDirectory) / "twin.jsonl"
                             : std::filesystem::path(m_resultPath);
    const std::string outputStem = outputPath.stem().string();
    const std::string labelFileName =
        outputStem == "twin"
            ? "labels.jsonl"
            : (outputStem.rfind("twin_", 0) == 0 ? "labels_" + outputStem.substr(5) + ".jsonl"
                                                  : outputStem + "_labels.jsonl");
    std::filesystem::path labelPath = outputPath.parent_path() / labelFileName;
    if (outputPath.has_parent_path())
    {
        std::filesystem::create_directories(outputPath.parent_path());
    }
    std::ofstream output(outputPath);
    if (!output)
    {
        throw std::runtime_error("Cannot open result output file: " + outputPath.string());
    }
    std::ofstream labelOutput(labelPath);
    if (!labelOutput)
    {
        throw std::runtime_error("Cannot open label output file: " + labelPath.string());
    }
    std::vector<std::string> nicStateLabels;
    std::vector<std::string> nodeStateLabels;
    std::vector<std::string> channelStateLabels;
    std::vector<std::string> dataFlowStateLabels;
    std::map<std::string, std::string> channelStateById;
    std::map<std::string, std::string> flowStateById;
    bool hasFaultState = false;
    bool hasCongestedState = false;

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
    std::map<std::pair<std::string, uint32_t>, std::string> channelIdByNodeInterface;
    std::map<std::string, std::vector<std::string>> routesByNode;
    std::map<std::string, std::vector<std::string>> actionsByNode;
    std::map<std::string, std::vector<std::string>> actionsByChannel;
    std::map<std::string, std::vector<std::string>> actionsByInterface;
    std::map<std::string, std::vector<std::string>> actionsByFlow;
    std::map<std::tuple<std::string, std::string, std::string>, std::vector<std::string>>
        routeDestinationsByKey;
    std::map<std::string, std::vector<std::string>> pathChannelsByFlow;
    std::map<std::string, std::vector<std::string>> originatedFlowsByNode;
    std::map<std::string, std::vector<std::string>> pathNodesByFlow;
    std::map<std::string, std::vector<std::string>> carriedFlowsByChannel;

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
        channelIdByNodeInterface[{iface.node, iface.interfaceIndex}] = iface.channelId;
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
    auto buildFlowPath = [&](const std::string& src, const std::string& dst) {
        std::vector<std::string> pathNodes;
        std::vector<std::string> pathChannels;
        pathNodes.push_back(src);
        if (src == dst)
        {
            return std::make_pair(pathNodes, pathChannels);
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
            if (std::find(pathNodes.begin(), pathNodes.end(), nextNode) != pathNodes.end())
            {
                break;
            }
            auto channelIt =
                channelIdByNodeInterface.find({current, static_cast<uint32_t>(outInterface)});
            if (channelIt != channelIdByNodeInterface.end())
            {
                pathChannels.push_back(channelIt->second);
            }
            pathNodes.push_back(nextNode);
            if (nextNode == dst)
            {
                break;
            }
            current = nextNode;
        }
        return std::make_pair(pathNodes, pathChannels);
    };

    for (const auto& flow : m_flowRecords)
    {
        auto [pathNodes, pathChannels] = buildFlowPath(flow.src, flow.dst);
        pathNodesByFlow[flow.id] = std::move(pathNodes);
        pathChannelsByFlow[flow.id] = pathChannels;
        for (const auto& channelId : pathChannels)
        {
            carriedFlowsByChannel[channelId].push_back(flow.id);
        }
    }

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
        else if (queueUtilization >= SATURATION_THRESHOLD)
        {
            state = "saturated";
        }
        output << "{\"entity_type\":\"nic\",\"entity_id\":"
               << JsonString(outputInterfaceIdById[iface.id])
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
        nicStateLabels.push_back(
            "{\"entity_id\":" + JsonString(outputInterfaceIdById[iface.id]) +
            ",\"label\":" + JsonString(state) + "}");
        hasFaultState = hasFaultState || state == "disabled";
        hasCongestedState = hasCongestedState || state == "saturated";
    }

    for (const auto& node : m_nodeRecords)
    {
        auto counters = m_nodeCounters.find(node.id) == m_nodeCounters.end()
                            ? PacketCounters{}
                            : m_nodeCounters.at(node.id);
        output << "{\"entity_type\":\"node\",\"entity_id\":" << JsonString(node.id)
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
        nodeStateLabels.push_back("{\"entity_id\":" + JsonString(node.id) +
                                  ",\"label\":" + JsonString(node.state) + "}");
        hasFaultState = hasFaultState || node.state == "disabled" ||
                        node.state == "routing_failed";
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
               << ",\"properties\":{\"capacity_mbps\":" << channel.nominalCapacityMbps
               << ",\"effective_capacity_mbps\":" << channel.effectiveCapacityMbps
               << ",\"available_bandwidth_mbps\":" << available
               << ",\"utilization\":" << utilization
               << ",\"delay_ms\":" << m_defaultChannelDelay.GetMilliSeconds() << "}"
               << ",\"relations\":{\"connects\":" << JsonStringArray(outputInterfaceIds)
               << ",\"carries\":" << JsonStringArray(carriedFlowsByChannel[channel.id]) << "}";
        auto actionIt = actionsByChannel.find(channel.id);
        if (actionIt != actionsByChannel.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
        channelStateLabels.push_back("{\"entity_id\":" + JsonString(channel.id) +
                                     ",\"label\":" + JsonString(state) + "}");
        channelStateById[channel.id] = state;
        hasFaultState = hasFaultState || state == "disabled" || state == "degraded";
        hasCongestedState = hasCongestedState || state == "saturated";
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
        if (hasStats && stats.rxPackets > 0)
        {
            successfulFlows++;
        }
        std::string state = "normal";
        if (!hasStats || stats.txPackets == 0 || stats.rxPackets == 0)
        {
            state = "failed";
        }
        else if (stats.lostPackets > 0)
        {
            state = "unstable";
        }
        else if (throughputMbps < flow.nominalDemandMbps * 0.95)
        {
            state = "degraded";
        }
        const auto& pathNodes = pathNodesByFlow[flow.id];
        output << "{\"entity_type\":\"data_flow\",\"entity_id\":" << JsonString(flow.id)
               << ",\"properties\":{\"demand_mbps\":" << flow.nominalDemandMbps
               << ",\"tx_packets\":" << (hasStats ? stats.txPackets : 0)
               << ",\"rx_packets\":" << (hasStats ? stats.rxPackets : 0)
               << ",\"lost_packets\":" << (hasStats ? stats.lostPackets : 0)
               << ",\"throughput_mbps\":" << throughputMbps
               << ",\"average_delay_ms\":" << avgDelayMs << "}"
               << ",\"relations\":{\"source_node\":" << JsonString(flow.src)
               << ",\"destination_node\":" << JsonString(flow.dst)
               << ",\"path_nodes\":" << JsonStringArray(pathNodes)
               << ",\"path_channels\":" << JsonStringArray(pathChannelsByFlow[flow.id]) << "}";
        auto actionIt = actionsByFlow.find(flow.id);
        if (actionIt != actionsByFlow.end())
        {
            output << ",\"action\":" << JsonRawArray(actionIt->second);
        }
        output << "}\n";
        dataFlowStateLabels.push_back("{\"entity_id\":" + JsonString(flow.id) +
                                      ",\"label\":" + JsonString(state) + "}");
        flowStateById[flow.id] = state;
        hasFaultState = hasFaultState || state == "failed";
    }

    const double totalThroughputMbps = totalRxBytes * 8.0 / duration / 1000000.0;
    const double averageDelayMs = totalRxPackets > 0 ? delayPacketSumMs / totalRxPackets : 0.0;
    const double averageLoss = SafeRatio(totalLostPackets, totalTxPackets);
    const double reachabilityRatio = SafeRatio(successfulFlows, m_flowRecords.size());
    const double meanUtilization = utilizationCount > 0 ? utilizationSum / utilizationCount : 0.0;
    const double loadVariance =
        utilizationCount > 0 ? utilizationSquareSum / utilizationCount - meanUtilization * meanUtilization : 0.0;
    const std::string networkState =
        hasFaultState ? "faulty" : (hasCongestedState ? "congested" : "normal");

    std::vector<std::string> bottleneckLabels;
    std::vector<std::string> congestionPatternLabels;
    std::vector<std::string> channelSaturationCauseLabels;
    std::vector<std::string> bandwidthConstraintLabels;
    std::vector<std::string> flowFailureCauseLabels;
    std::vector<std::string> flowFailureTypeLabels;
    auto isPhysicallyReachable = [&](const std::string& sourceNode,
                                     const std::string& destinationNode,
                                     const std::string& restoredNode,
                                     const std::string& restoredChannel) {
        std::map<std::string, std::vector<std::string>> adjacency;
        for (const auto& channel : m_channelRecords)
        {
            if (channel.state == "disabled" && channel.id != restoredChannel)
            {
                continue;
            }
            if (channel.interfaceIds.size() != 2)
            {
                continue;
            }

            bool operational = true;
            for (const auto& interfaceId : channel.interfaceIds)
            {
                auto interfaceIt = m_interfaceIndexById.find(interfaceId);
                if (interfaceIt == m_interfaceIndexById.end())
                {
                    operational = false;
                    break;
                }
                const auto& iface = m_interfaceRecords[interfaceIt->second];
                if (iface.state == "disabled" && channel.id != restoredChannel)
                {
                    operational = false;
                    break;
                }
                auto nodeIt = m_nodeIndexById.find(iface.node);
                if (nodeIt == m_nodeIndexById.end() ||
                    (m_nodeRecords[nodeIt->second].state == "disabled" &&
                     iface.node != restoredNode))
                {
                    operational = false;
                    break;
                }
            }
            if (!operational)
            {
                continue;
            }
            adjacency[channel.src].push_back(channel.dst);
            adjacency[channel.dst].push_back(channel.src);
        }

        std::set<std::string> visited{sourceNode};
        std::vector<std::string> pending{sourceNode};
        while (!pending.empty())
        {
            const std::string current = pending.back();
            pending.pop_back();
            if (current == destinationNode)
            {
                return true;
            }
            for (const auto& neighbor : adjacency[current])
            {
                if (visited.insert(neighbor).second)
                {
                    pending.push_back(neighbor);
                }
            }
        }
        return false;
    };
    for (const auto& channel : m_channelRecords)
    {
        auto stateIt = channelStateById.find(channel.id);
        auto carriedIt = carriedFlowsByChannel.find(channel.id);
        if (stateIt == channelStateById.end() || stateIt->second != "saturated" ||
            carriedIt == carriedFlowsByChannel.end() || carriedIt->second.empty())
        {
            continue;
        }

        double totalDemandMbps = 0.0;
        double largestDemandMbps = 0.0;
        bool completeEvidence = true;
        for (const auto& flowId : carriedIt->second)
        {
            auto flowIndexIt = m_flowIndexById.find(flowId);
            if (flowIndexIt == m_flowIndexById.end())
            {
                completeEvidence = false;
                break;
            }
            const double demandMbps = m_flowRecords[flowIndexIt->second].nominalDemandMbps;
            totalDemandMbps += demandMbps;
            largestDemandMbps = std::max(largestDemandMbps, demandMbps);
        }
        if (!completeEvidence || largestDemandMbps <= 0.0)
        {
            continue;
        }

        const double otherDemandMbps = totalDemandMbps - largestDemandMbps;
        const std::string cause = largestDemandMbps > otherDemandMbps
                                      ? "single_large_flow"
                                      : "multiple_flow_aggregation";
        channelSaturationCauseLabels.push_back(
            "{\"channel_id\":" + JsonString(channel.id) +
            ",\"label\":" + JsonString(cause) + "}");
    }
    for (const auto& flow : m_flowRecords)
    {
        const auto& pathChannels = pathChannelsByFlow[flow.id];
        std::vector<std::string> saturatedChannelIds;
        bool validPath = !pathChannels.empty();
        bool hasInsufficientCapacity = false;
        bool hasTrafficCongestion = false;
        for (const auto& channelId : pathChannels)
        {
            auto stateIt = channelStateById.find(channelId);
            auto channelIt = channelById.find(channelId);
            if (stateIt == channelStateById.end() || channelIt == channelById.end() ||
                stateIt->second == "disabled")
            {
                validPath = false;
                break;
            }
            if (flow.nominalDemandMbps >= channelIt->second.effectiveCapacityMbps)
            {
                hasInsufficientCapacity = true;
            }
            if (stateIt->second == "saturated")
            {
                hasTrafficCongestion = true;
                saturatedChannelIds.push_back(channelId);
            }
            else if (stateIt->second != "normal")
            {
                validPath = false;
                break;
            }
        }
        if (validPath && !saturatedChannelIds.empty())
        {
            const std::string congestionPattern = saturatedChannelIds.size() == 1
                                                      ? "single_channel_bottleneck"
                                                      : "multi_channel_saturation";
            congestionPatternLabels.push_back(
                "{\"data_flow_id\":" + JsonString(flow.id) +
                ",\"label\":" + JsonString(congestionPattern) + "}");
            if (saturatedChannelIds.size() == 1)
            {
                bottleneckLabels.push_back(
                    "{\"data_flow_id\":" + JsonString(flow.id) +
                    ",\"channel_id\":" + JsonString(saturatedChannelIds.front()) + "}");
            }
        }
        if (validPath && (hasTrafficCongestion || hasInsufficientCapacity))
        {
            const std::string constraint = hasTrafficCongestion && hasInsufficientCapacity
                                               ? "both"
                                           : hasTrafficCongestion ? "traffic_congestion"
                                                                  : "insufficient_channel_capacity";
            bandwidthConstraintLabels.push_back("{\"data_flow_id\":" + JsonString(flow.id) +
                                                ",\"label\":" + JsonString(constraint) + "}");
        }

        if (flowStateById[flow.id] == "failed")
        {
            std::vector<std::string> faultEntityIds;
            auto addFaultEntity = [&faultEntityIds](const std::string& entityId) {
                if (std::find(faultEntityIds.begin(), faultEntityIds.end(), entityId) ==
                    faultEntityIds.end())
                {
                    faultEntityIds.push_back(entityId);
                }
            };
            if (!isPhysicallyReachable(flow.src, flow.dst, "", ""))
            {
                for (const auto& node : m_nodeRecords)
                {
                    if (node.state == "disabled" &&
                        isPhysicallyReachable(flow.src, flow.dst, node.id, ""))
                    {
                        addFaultEntity(node.id);
                    }
                }
                for (const auto& channel : m_channelRecords)
                {
                    bool hasChannelFailure = channel.state == "disabled";
                    for (const auto& interfaceId : channel.interfaceIds)
                    {
                        auto interfaceIt = m_interfaceIndexById.find(interfaceId);
                        if (interfaceIt != m_interfaceIndexById.end() &&
                            m_interfaceRecords[interfaceIt->second].state == "disabled")
                        {
                            hasChannelFailure = true;
                        }
                    }
                    if (hasChannelFailure &&
                        isPhysicallyReachable(flow.src, flow.dst, "", channel.id))
                    {
                        addFaultEntity(channel.id);
                    }
                }
            }
            for (const auto& nodeId : pathNodesByFlow[flow.id])
            {
                auto nodeIt = m_nodeIndexById.find(nodeId);
                if (nodeIt == m_nodeIndexById.end())
                {
                    continue;
                }
                const auto& node = m_nodeRecords[nodeIt->second];
                if (node.state == "disabled")
                {
                    addFaultEntity(nodeId);
                }
                else if (node.state == "routing_failed")
                {
                    const uint32_t srcIndex = NetworkSceneNodeNumber(nodeId) - 1;
                    const uint32_t dstIndex = NetworkSceneNodeNumber(flow.dst) - 1;
                    if (srcIndex < m_routingMatrix.size() &&
                        dstIndex < m_routingMatrix[srcIndex].size() &&
                        m_routingMatrix[srcIndex][dstIndex] <= 0)
                    {
                        addFaultEntity(nodeId);
                    }
                }
            }
            for (const auto& channelId : pathChannels)
            {
                auto channelIt = channelById.find(channelId);
                if (channelIt != channelById.end() && channelIt->second.state == "disabled")
                {
                    addFaultEntity(channelId);
                }
                if (channelIt != channelById.end())
                {
                    for (const auto& interfaceId : channelIt->second.interfaceIds)
                    {
                        auto interfaceIt = m_interfaceIndexById.find(interfaceId);
                        if (interfaceIt != m_interfaceIndexById.end() &&
                            m_interfaceRecords[interfaceIt->second].state == "disabled")
                        {
                            addFaultEntity(channelId);
                        }
                    }
                }
            }
            if (faultEntityIds.size() == 1)
            {
                const std::string& faultEntityId = faultEntityIds[0];
                flowFailureCauseLabels.push_back(
                    "{\"data_flow_id\":" + JsonString(flow.id) +
                    ",\"entity_id\":" + JsonString(faultEntityId) + "}");

                std::string failureType;
                if (channelById.find(faultEntityId) != channelById.end())
                {
                    failureType = "channel_failure";
                }
                else
                {
                    auto nodeIt = m_nodeIndexById.find(faultEntityId);
                    if (nodeIt != m_nodeIndexById.end())
                    {
                        const std::string& nodeState = m_nodeRecords[nodeIt->second].state;
                        if (nodeState == "disabled")
                        {
                            failureType = "node_crash";
                        }
                        else if (nodeState == "routing_failed")
                        {
                            failureType = "routing_failure";
                        }
                    }
                }
                if (!failureType.empty())
                {
                    flowFailureTypeLabels.push_back(
                        "{\"data_flow_id\":" + JsonString(flow.id) +
                        ",\"label\":" + JsonString(failureType) + "}");
                }
            }
        }
    }

    labelOutput << "{\"label_type\":\"node_state\",\"label\":"
                << JsonRawArray(nodeStateLabels) << "}\n";
    labelOutput << "{\"label_type\":\"nic_state\",\"label\":"
                << JsonRawArray(nicStateLabels) << "}\n";
    labelOutput << "{\"label_type\":\"channel_state\",\"label\":"
                << JsonRawArray(channelStateLabels) << "}\n";
    labelOutput << "{\"label_type\":\"data_flow_state\",\"label\":"
                << JsonRawArray(dataFlowStateLabels) << "}\n";
    labelOutput << "{\"label_type\":\"network_state\",\"label\":"
                << JsonString(networkState) << "}\n";
    labelOutput << "{\"label_type\":\"bottleneck\",\"label\":"
                << JsonRawArray(bottleneckLabels) << "}\n";
    labelOutput << "{\"label_type\":\"data_flow_congestion_pattern\",\"label\":"
                << JsonRawArray(congestionPatternLabels) << "}\n";
    labelOutput << "{\"label_type\":\"channel_saturation_cause\",\"label\":"
                << JsonRawArray(channelSaturationCauseLabels) << "}\n";
    labelOutput << "{\"label_type\":\"data_flow_bandwidth_constraint\",\"label\":"
                << JsonRawArray(bandwidthConstraintLabels) << "}\n";
    labelOutput << "{\"label_type\":\"data_flow_failure_cause\",\"label\":"
                << JsonRawArray(flowFailureCauseLabels) << "}\n";
    labelOutput << "{\"label_type\":\"data_flow_failure_type\",\"label\":"
                << JsonRawArray(flowFailureTypeLabels) << "}\n";

    (void)totalThroughputMbps;
    (void)averageDelayMs;
    (void)averageLoss;
    (void)reachabilityRatio;
    (void)loadVariance;
}

} // namespace ns3
