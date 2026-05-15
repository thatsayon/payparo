from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model

from app.messaging.models import Conversation, Message, Block, Report
from app.messaging.serializers import ConversationSerializer, MessageSerializer, BlockSerializer, ReportSerializer

User = get_user_model()

class ConversationListCreateView(generics.ListCreateAPIView):
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(participants=self.request.user)

    def create(self, request, *args, **kwargs):
        other_username = request.data.get('username')
        other_user_id = request.data.get('user_id')
        
        if not other_username and not other_user_id:
            return Response({"error": "Provide username or user_id"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            if other_username:
                other_user = User.objects.get(username=other_username)
            else:
                other_user = User.objects.get(id=other_user_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        if other_user == request.user:
            return Response({"error": "Cannot create conversation with yourself"}, status=status.HTTP_400_BAD_REQUEST)

        # Check if they already have a conversation
        conversations = Conversation.objects.filter(participants=request.user).filter(participants=other_user)
        if conversations.exists():
            serializer = self.get_serializer(conversations.first())
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        # Create new one
        conversation = Conversation.objects.create()
        conversation.participants.add(request.user, other_user)
        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class MessageListView(generics.ListAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        conversation_id = self.kwargs.get('conversation_id')
        return Message.objects.filter(
            conversation_id=conversation_id,
            conversation__participants=self.request.user
        ).order_by('-created_at')
        
class BlockUserView(generics.CreateAPIView):
    serializer_class = BlockSerializer
    permission_classes = [IsAuthenticated]
    
    def perform_create(self, serializer):
        blocked_user_id = self.request.data.get('blocked')
        blocked_user = get_object_or_404(User, id=blocked_user_id)
        serializer.save(blocker=self.request.user, blocked=blocked_user)
        
class UnblockUserView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request, user_id):
        blocked_user = get_object_or_404(User, id=user_id)
        Block.objects.filter(blocker=request.user, blocked=blocked_user).delete()
        return Response({"message": "User unblocked successfully."}, status=status.HTTP_200_OK)

class BlockedUsersListView(generics.ListAPIView):
    serializer_class = BlockSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return Block.objects.filter(blocker=self.request.user)

class ReportUserView(generics.CreateAPIView):
    serializer_class = ReportSerializer
    permission_classes = [IsAuthenticated]
    
    def perform_create(self, serializer):
        reported_user_id = self.request.data.get('reported')
        reported_user = get_object_or_404(User, id=reported_user_id)
        serializer.save(reporter=self.request.user, reported=reported_user)

class ImageUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get('image')
        if not file:
            return Response({"error": "No image provided"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            import cloudinary.uploader
            upload_result = cloudinary.uploader.upload(file)
            return Response({"image_url": upload_result.get("secure_url")}, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MarkAsReadView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request, conversation_id):
        Message.objects.filter(
            conversation_id=conversation_id,
            is_read=False
        ).exclude(sender=request.user).update(is_read=True)
        return Response({"message": "Marked as read"}, status=status.HTTP_200_OK)
