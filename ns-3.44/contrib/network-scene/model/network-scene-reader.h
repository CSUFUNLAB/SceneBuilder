#ifndef NETWORK_SCENE_READER_H
#define NETWORK_SCENE_READER_H

#include "network-scene-traffic.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

struct NetworkSceneNodeRow
{
    std::string id;
    std::string state;
};

struct NetworkSceneChannelRow
{
    std::string id;
    std::string src;
    std::string dst;
    double bandwidthMbps{0.0};
    double capacityMultiplier{1.0};
    std::string state;
};

struct NetworkSceneNicRow
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

struct NetworkSceneEventRow
{
    std::string id;
    double timeSeconds{0.0};
    std::string entityType;
    std::string entityId;
    std::string eventType;
    double rateMultiplier{1.0};
};

struct NetworkSceneData
{
    std::vector<NetworkSceneNodeRow> nodes;
    std::vector<NetworkSceneChannelRow> channels;
    std::vector<NetworkSceneNicRow> nics;
    std::vector<std::vector<int>> routingMatrix;
    std::vector<NetworkSceneTrafficPattern> traffic;
    std::vector<NetworkSceneEventRow> events;
    double sceneDurationSeconds{300.0};
};

std::string ResolveNetworkSceneDirectory(const std::string& value);
std::string NetworkSceneBaseName(const std::string& path);
NetworkSceneData ReadNetworkSceneData(const std::string& sceneDirectory, const std::string& eventFile = "");

} // namespace ns3

#endif /* NETWORK_SCENE_READER_H */
