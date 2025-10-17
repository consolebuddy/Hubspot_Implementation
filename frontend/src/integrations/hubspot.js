// hubspot.js
import { useState, useEffect } from 'react';
import { Box, Button, CircularProgress } from '@mui/material';
import axios from 'axios';

export const HubSpotIntegration = ({ user, org, integrationParams, setIntegrationParams }) => {
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);

  const handleConnectClick = async () => {
    try {
      setIsConnecting(true);
      const formData = new FormData();
      formData.append('user_id', user);
      formData.append('org_id', org);
      const { data: authURL } = await axios.post('http://localhost:8000/integrations/hubspot/authorize', formData);

      const win = window.open(authURL, 'HubSpot Authorization', 'width=600,height=700');
      const timer = window.setInterval(() => {
        if (win?.closed !== false) {
          window.clearInterval(timer);
          handleWindowClosed();
        }
      }, 500);
    } catch (e) {
      console.error(e);
      setIsConnecting(false);
    }
  };

  const handleWindowClosed = async () => {
    try {
      const formData = new FormData();
      formData.append('user_id', user);
      formData.append('org_id', org);
      const { data } = await axios.post('http://localhost:8000/integrations/hubspot/credentials', formData);
      setIntegrationParams({ ...(integrationParams || {}), type: 'HubSpot', credentials: JSON.stringify(data) });
      setIsConnected(true);
    } catch (e) {
      console.error('Failed to fetch credentials', e);
    } finally {
      setIsConnecting(false);
    }
  };

  useEffect(() => {
    if (!integrationParams?.credentials) setIsConnected(false);
  }, [integrationParams]);

  return (
    <Box sx={{ mt: 2 }}>
      <Button
        onClick={handleConnectClick}
        variant='contained'
        disabled={isConnecting}
        style={{
          pointerEvents: isConnected ? 'none' : 'auto',
          cursor: isConnected ? 'default' : 'pointer',
          opacity: isConnected ? 1 : undefined
        }}
      >
        {isConnected ? 'HubSpot Connected' : isConnecting ? <CircularProgress size={20} /> : 'Connect to HubSpot'}
      </Button>
    </Box>
  );
};
