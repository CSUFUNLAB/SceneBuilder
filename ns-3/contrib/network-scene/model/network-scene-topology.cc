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

NetworkSceneInterfacesByChannel
GroupNetworkSceneInterfacesByChannel(const std::vector<NetworkSceneInterfaceRow>& interfaces)
{
    NetworkSceneInterfacesByChannel interfacesByChannel;
    for (const auto& interface : interfaces)
    {
        interfacesByChannel[interface.channelId].push_back(interface);
    }
    return interfacesByChannel;
}

std::vector<NetworkSceneInterfaceRow>
SortedNetworkSceneChannelInterfaces(const NetworkSceneInterfacesByChannel& interfacesByChannel, const std::string& channelId)
{
    auto interfacesIt = interfacesByChannel.find(channelId);
    if (interfacesIt == interfacesByChannel.end() || interfacesIt->second.size() != 2)
    {
        throw std::runtime_error("Channel " + channelId + " must have exactly two interface rows");
    }

    auto channelInterfaces = interfacesIt->second;
    std::sort(channelInterfaces.begin(), channelInterfaces.end(), [](const NetworkSceneInterfaceRow& a, const NetworkSceneInterfaceRow& b) {
        if (a.node == b.node)
        {
            return a.interfaceIndex < b.interfaceIndex;
        }
        return NetworkSceneNodeNumber(a.node) < NetworkSceneNodeNumber(b.node);
    });
    return channelInterfaces;
}

} // namespace ns3
