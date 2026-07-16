#ifndef NETWORK_SCENE_TOPOLOGY_H
#define NETWORK_SCENE_TOPOLOGY_H

#include "network-scene-reader.h"

#include <string>
#include <unordered_map>
#include <vector>

namespace ns3
{

using NetworkSceneNicsByChannel = std::unordered_map<std::string, std::vector<NetworkSceneNicRow>>;

uint32_t NetworkSceneNodeNumber(const std::string& id);
NetworkSceneNicsByChannel GroupNetworkSceneNicsByChannel(const std::vector<NetworkSceneNicRow>& nics);
std::vector<NetworkSceneNicRow> SortedNetworkSceneChannelNics(const NetworkSceneNicsByChannel& nicsByChannel,
                                                              const std::string& channelId);

} // namespace ns3

#endif /* NETWORK_SCENE_TOPOLOGY_H */
