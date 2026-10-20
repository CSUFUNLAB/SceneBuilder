#ifndef NETWORK_SCENE_TOPOLOGY_H
#define NETWORK_SCENE_TOPOLOGY_H

#include "network-scene-reader.h"

#include <string>
#include <unordered_map>
#include <vector>

namespace ns3
{

using NetworkSceneInterfacesByChannel = std::unordered_map<std::string, std::vector<NetworkSceneInterfaceRow>>;

uint32_t NetworkSceneNodeNumber(const std::string& id);
NetworkSceneInterfacesByChannel GroupNetworkSceneInterfacesByChannel(const std::vector<NetworkSceneInterfaceRow>& interfaces);
std::vector<NetworkSceneInterfaceRow> SortedNetworkSceneChannelInterfaces(const NetworkSceneInterfacesByChannel& interfacesByChannel,
                                                              const std::string& channelId);

} // namespace ns3

#endif /* NETWORK_SCENE_TOPOLOGY_H */
