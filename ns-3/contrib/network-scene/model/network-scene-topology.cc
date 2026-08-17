#include "network-scene-topology.h"

#include <algorithm>
#include <stdexcept>

namespace ns3
{

uint32_t
NetworkSceneNodeNumber(const std::string& id)
{
    if (id.size() < 2)
    {
        throw std::runtime_error("Invalid node id: " + id);
    }
    return static_cast<uint32_t>(std::stoul(id.substr(1)));
}

NetworkSceneNicsByChannel
GroupNetworkSceneNicsByChannel(const std::vector<NetworkSceneNicRow>& nics)
{
    NetworkSceneNicsByChannel nicsByChannel;
    for (const auto& nic : nics)
    {
        nicsByChannel[nic.channelId].push_back(nic);
    }
    return nicsByChannel;
}

std::vector<NetworkSceneNicRow>
SortedNetworkSceneChannelNics(const NetworkSceneNicsByChannel& nicsByChannel, const std::string& channelId)
{
    auto nicsIt = nicsByChannel.find(channelId);
    if (nicsIt == nicsByChannel.end() || nicsIt->second.size() != 2)
    {
        throw std::runtime_error("Channel " + channelId + " must have exactly two NIC rows");
    }

    auto channelNics = nicsIt->second;
    std::sort(channelNics.begin(), channelNics.end(), [](const NetworkSceneNicRow& a, const NetworkSceneNicRow& b) {
        if (a.node == b.node)
        {
            return a.interfaceIndex < b.interfaceIndex;
        }
        return NetworkSceneNodeNumber(a.node) < NetworkSceneNodeNumber(b.node);
    });
    return channelNics;
}

} // namespace ns3
